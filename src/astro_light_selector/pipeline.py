"""量測 → 分組 → 挑片 的流程，命令列跟視窗介面共用。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .grouping import group_frames
from .metrics import FITS_SUFFIXES, FrameMetrics, measure
from .mover import moved_away
from .scoring import ScoreConfig, ScoreResult
from .selector import Decision, Thresholds, select, select_by_score

REPORT_NAME = "selection_report.csv"
REJECT_DIR_NAME = "rejected"

ProgressFn = Callable[[int, int, FrameMetrics], None]


class Cancelled(Exception):
    """量測被使用者中止。"""


def find_fits(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in FITS_SUFFIXES)


def collect_files(folder: Path, reject_dir: Path) -> tuple[list[Path], dict[str, str]]:
    """回傳 (要量測的檔案, {目前位置: 原位置})。

    之前被搬去 reject 的也要算進同一批：門檻是相對整批算的，只看留下來的會越挑越嚴。
    """
    away = moved_away(reject_dir, folder)
    home_of = {str(cur): str(home) for home, cur in away.items()}
    files = sorted(find_fits(folder) + list(away.values()),
                   key=lambda p: Path(home_of.get(str(p), p)).name)
    return files, home_of


def measure_files(files: list[Path], workers: int = 1, progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None,
                  home_of: dict[str, str] | None = None) -> list[FrameMetrics]:
    """量測每個檔案，結果照 files 的順序回傳。

    progress(已完成張數, 總張數, 結果) 每量完一張呼叫一次；cancel 被設起來就丟 Cancelled。
    home_of 裡有的檔案，結果的檔名換成原位置（之前被搬去 reject 的）。
    """
    home_of = home_of or {}
    n = len(files)
    results: list[FrameMetrics | None] = [None] * n

    def done(i: int, m: FrameMetrics, count: int) -> None:
        m.file = home_of.get(m.file, m.file)
        results[i] = m
        if progress is not None:
            progress(count, n, m)

    if workers > 1 and n > 1:
        ex = ProcessPoolExecutor(max_workers=min(workers, n))
        try:
            futures = {ex.submit(measure, f): i for i, f in enumerate(files)}
            for count, fut in enumerate(as_completed(futures), 1):
                if cancel is not None and cancel.is_set():
                    raise Cancelled
                done(futures[fut], fut.result(), count)
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
    else:
        for i, f in enumerate(files):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            done(i, measure(f), i + 1)
    return results  # type: ignore[return-value]


@dataclass
class Selection:
    decisions: list[Decision]
    group_sizes: dict[str, int]                # 分組標籤 -> 張數
    results: dict[str, ScoreResult | None]     # 評分模式各組的結果；整組量不出來為 None

    @property
    def thresholds(self) -> dict[str, float]:
        return {g: r.threshold for g, r in self.results.items() if r is not None}

    @property
    def n_keep(self) -> int:
        return sum(d.keep for d in self.decisions)


def decide(frames: list[FrameMetrics], group_keys: tuple[str, ...], mode: str = "score",
           cfg: ScoreConfig | None = None, th: Thresholds | None = None) -> Selection:
    """分組後各組分開挑片，decisions 照 frames 的順序。"""
    cfg = cfg or ScoreConfig()
    th = th or Thresholds()
    groups = group_frames(frames, group_keys)
    by_file: dict[str, Decision] = {}
    results: dict[str, ScoreResult | None] = {}
    for label, members in groups.items():
        if mode == "score":
            group_decisions, results[label] = select_by_score(members, cfg)
        else:
            group_decisions = select(members, th)
        for d in group_decisions:
            d.group = label
            by_file[d.metrics.file] = d
    return Selection(
        decisions=[by_file[f.file] for f in frames],
        group_sizes={label: len(members) for label, members in groups.items()},
        results=results,
    )
