"""量測 → 分組 → 挑片 的流程，命令列跟視窗介面共用。"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .grouping import EXPOSURE_TOLERANCE, group_frames
from .metrics import FrameMetrics, is_image, measure
from .mover import moved_away, remove_empty_dirs, restore_rejected, sync_files
from .scoring import ScoreConfig, ScoreResult
from .selector import Decision, Reason, Thresholds, select, select_by_score

REPORT_NAME = "selection_report.csv"
REJECT_DIR_NAME = "rejected"
# 子資料夾模式不進去的資料夾：名稱含這些字（不分大小寫）的通常是校正檔或疊圖軟體的輸出，
# 掃進來暗場會因為量不到星被淘汰、搬走
SKIP_DIR_WORDS = ("dark", "flat", "bias", "master", "calibrated")

ProgressFn = Callable[[int, int, FrameMetrics], None]


class Cancelled(Exception):
    """量測被使用者中止。"""


def find_fits(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and is_image(p))


def skip_dir(name: str) -> bool:
    low = name.lower()
    return name.startswith(".") or low == REJECT_DIR_NAME or any(w in low for w in SKIP_DIR_WORDS)


def image_dirs(folder: Path, recursive: bool = False, exclude: Path | None = None) -> list[Path]:
    """要挑片的資料夾：folder 本身；子資料夾模式再加上底下所有沒被跳過的子資料夾（不限層數）。

    exclude：自訂的淘汰片資料夾放在 folder 裡面時，不能把它當成要挑片的資料夾掃進來。
    """
    if not recursive:
        return [folder]
    ex = exclude.resolve() if exclude is not None else None
    dirs = []
    for dirpath, dirnames, _ in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames
                             if not skip_dir(d) and (ex is None or (Path(dirpath) / d).resolve() != ex))
        dirs.append(Path(dirpath))
    return dirs


def should_recurse(folder: Path) -> bool:
    """開啟的資料夾本身沒有影像、子資料夾裡有（例如單眼按日期分的多晚資料夾）：自動改成子資料夾模式。"""
    if not folder.is_dir() or find_fits(folder) or moved_away(folder / REJECT_DIR_NAME, folder):
        return False
    return any(find_fits(d) or moved_away(d / REJECT_DIR_NAME, d) for d in image_dirs(folder, True)[1:])


def frame_key(file: str | Path, root: Path | None) -> str:
    """報表、手動覆寫、清單用來認片的代號：相對於開啟資料夾的路徑（用 / 分隔）。

    只開一層資料夾時就是檔名，跟舊版報表一樣；子資料夾模式下單眼的檔名常常每晚重複，要帶資料夾才分得開。
    """
    p = Path(file)
    if root is not None:
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            try:
                return p.resolve().relative_to(Path(root).resolve()).as_posix()
            except (ValueError, OSError):
                pass
    return p.name


def reject_dir_for(directory: Path, folder: Path, reject_dir: Path | None, recursive: bool) -> Path:
    """directory 裡的淘汰片搬去哪。

    - 只開一層：reject_dir（沒指定就是 folder/rejected）
    - 子資料夾模式、沒指定：directory 自己的 rejected/
    - 子資料夾模式、有指定：reject_dir 底下照原本的子資料夾結構放（各晚的檔名常常重複，攤平會撞名）
    """
    if not recursive:
        return reject_dir if reject_dir is not None else folder / REJECT_DIR_NAME
    if reject_dir is None:
        return directory / REJECT_DIR_NAME
    rel = frame_key(directory, folder)
    return reject_dir if rel == "." else reject_dir / rel


def collect_files(folder: Path, reject_dir: Path | None = None,
                  recursive: bool = False) -> tuple[list[Path], dict[str, str]]:
    """回傳 (要量測的檔案, {目前位置: 原位置})。

    之前被搬去 reject 的也要算進同一批：門檻是相對整批算的，只看留下來的會越挑越嚴。
    每個資料夾的淘汰片在哪見 reject_dir_for。
    """
    files: list[Path] = []
    home_of: dict[str, str] = {}
    for d in image_dirs(folder, recursive, reject_dir):
        away = moved_away(reject_dir_for(d, folder, reject_dir, recursive), d)
        home_of.update({str(cur): str(home) for home, cur in away.items()})
        files += find_fits(d) + list(away.values())
    files.sort(key=lambda p: frame_key(home_of.get(str(p), p), folder))
    return files, home_of


def move_files(decisions: list[Decision], reject_dir: Path | None, recursive: bool = False,
               dry_run: bool = False, folder: Path | None = None) -> tuple[list[Path], list[Path]]:
    """照挑片結果搬檔（見 mover.sync_files）；子資料夾模式下每個資料夾分開搬，搬去哪見 reject_dir_for。

    folder 是開啟的資料夾：子資料夾模式又指定了 reject_dir 時，用它算每張的相對位置。
    """
    if not recursive:
        return sync_files(decisions, reject_dir, dry_run)
    if reject_dir is not None and folder is None:
        raise ValueError("子資料夾模式指定淘汰片資料夾時要給 folder")
    by_dir: dict[Path, list[Decision]] = {}
    for d in decisions:
        by_dir.setdefault(Path(d.metrics.file).resolve().parent, []).append(d)
    out: list[Path] = []
    back: list[Path] = []
    for parent, members in by_dir.items():
        o, b = sync_files(members, reject_dir_for(parent, folder or parent, reject_dir, True), dry_run)
        out += o
        back += b
    return out, back


def restore_files(folder: Path, reject_dir: Path | None, recursive: bool = False,
                  dry_run: bool = False) -> list[Path]:
    """淘汰片全部搬回原位（見 mover.restore_rejected）；子資料夾模式下每個資料夾的淘汰片都搬回。"""
    if not recursive:
        return restore_rejected(reject_dir, folder, dry_run)
    restored: list[Path] = []
    for d in image_dirs(folder, True, reject_dir):
        rd = reject_dir_for(d, folder, reject_dir, True)
        if rd.is_dir():
            restored += restore_rejected(rd, d, dry_run)
    if reject_dir is not None and not dry_run:
        remove_empty_dirs(reject_dir)  # 照子資料夾結構建的空資料夾一起收掉
    return restored


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

    # 只有一張也放到子行程量：視窗監看時一次常只進來一張，在視窗的行程裡量會讓介面卡住
    if workers > 1:
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
           cfg: ScoreConfig | None = None, th: Thresholds | None = None,
           exposure_tolerance: float = EXPOSURE_TOLERANCE,
           overrides: dict[str, str] | None = None, root: Path | None = None) -> Selection:
    """分組後各組分開挑片，decisions 照 frames 的順序。曝光差 exposure_tolerance 秒以內算同一組。

    overrides：{frame_key: "keep" / "reject"}，使用者手動覆寫。覆寫的片照使用者決定，原因欄保留自動判斷；
    自動門檻仍用整批（含覆寫的片）計算，覆寫只改最後的結果。root 是開啟的資料夾（算 frame_key、依子資料夾分組用）。
    """
    cfg = cfg or ScoreConfig()
    th = th or Thresholds()
    groups = group_frames(frames, group_keys, exposure_tolerance, root)
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
    for d in by_file.values():
        choice = (overrides or {}).get(frame_key(d.metrics.file, root))
        if choice in ("keep", "reject"):
            d.override = choice
            d.keep = choice == "keep"
            d.reasons = [Reason(f"manual_{choice}"), *d.reasons]
    return Selection(
        decisions=[by_file[f.file] for f in frames],
        group_sizes={label: len(members) for label, members in groups.items()},
        results=results,
    )
