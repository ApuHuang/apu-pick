"""命令列介面。"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .metrics import FrameMetrics, measure
from .mover import move_rejected
from .report import print_summary, read_csv, write_csv
from .selector import Thresholds, select

FITS_SUFFIXES = {".fit", ".fits", ".fts"}


def find_fits(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in FITS_SUFFIXES)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="astro_light_selector",
        description="天文 light frame 自動挑片：計算 FWHM / 星點數 / 離心率 / 背景，"
                    "將不合格的搬到 rejected 資料夾。",
    )
    p.add_argument("folder", type=Path, help="放 light frames 的資料夾")
    p.add_argument("--reject-dir", type=Path, default=None,
                   help="reject 檔案搬去的資料夾（預設 <folder>/rejected）")
    p.add_argument("--report", type=Path, default=None,
                   help="CSV 報表路徑（預設 <folder>/selection_report.csv）")
    p.add_argument("--dry-run", action="store_true", help="只分析與輸出報表，不搬檔案")
    p.add_argument("--workers", type=int, default=1, help="平行處理數（預設 1）")
    p.add_argument("--from-report", type=Path, default=None,
                   help="不重新量測，直接讀之前的 CSV 報表重新套門檻（調參數用）")

    g = p.add_argument_group("相對門檻（中位數 ± k×MAD）")
    g.add_argument("-k", type=float, default=None, help="一次設定下面四個 k（越小越嚴）")
    g.add_argument("--fwhm-k", type=float, default=None, help="預設 1.5")
    g.add_argument("--stars-k", type=float, default=None, help="預設 2.0")
    g.add_argument("--ecc-k", type=float, default=None, help="預設 1.5")
    g.add_argument("--background-k", type=float, default=None, help="預設 3.0")

    g = p.add_argument_group("絕對門檻（設 -1 表示不使用）")
    g.add_argument("--max-fwhm", type=float, default=-1, help="FWHM 上限（像素）")
    g.add_argument("--min-stars", type=int, default=20, help="星點數下限")
    g.add_argument("--max-ecc", type=float, default=0.7, help="離心率上限")
    g.add_argument("--max-saturated", type=float, default=-1, help="飽和像素比例上限")
    return p


def _opt(v: float) -> float | None:
    return None if v < 0 else v


def _progress(i: int, n: int, m: FrameMetrics) -> None:
    name = Path(m.file).name
    if m.error:
        print(f"[{i}/{n}] {name}: ERROR {m.error}")
    else:
        print(f"[{i}/{n}] {name}: stars={m.n_stars} fwhm={m.fwhm:.2f} "
              f"ecc={m.eccentricity:.2f} bkg={m.background:.0f}")


def measure_all(files: list[Path], workers: int) -> list[FrameMetrics]:
    frames: list[FrameMetrics] = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, m in enumerate(ex.map(measure, files), 1):
                _progress(i, len(files), m)
                frames.append(m)
    else:
        for i, f in enumerate(files, 1):
            m = measure(f)
            _progress(i, len(files), m)
            frames.append(m)
    return frames


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    folder: Path = args.folder
    if not folder.is_dir():
        print(f"找不到資料夾: {folder}", file=sys.stderr)
        return 1

    files = find_fits(folder)
    if not files:
        print(f"{folder} 內沒有 FITS 檔案", file=sys.stderr)
        return 1

    if args.from_report:
        frames = read_csv(args.from_report, folder)
        print(f"從 {args.from_report} 讀入 {len(frames)} 張的量測結果")
    else:
        print(f"分析 {len(files)} 張影像...")
        frames = measure_all(files, args.workers)

    defaults = Thresholds()

    def k_of(v: float | None, default: float) -> float:
        if v is not None:
            return v
        return args.k if args.k is not None else default

    th = Thresholds(
        fwhm_k=k_of(args.fwhm_k, defaults.fwhm_k),
        stars_k=k_of(args.stars_k, defaults.stars_k),
        ecc_k=k_of(args.ecc_k, defaults.ecc_k),
        background_k=k_of(args.background_k, defaults.background_k),
        max_fwhm=_opt(args.max_fwhm),
        min_stars=None if args.min_stars < 0 else args.min_stars,
        max_eccentricity=_opt(args.max_ecc),
        max_saturated_frac=_opt(args.max_saturated),
    )
    decisions = select(frames, th)

    report = args.report or folder / "selection_report.csv"
    write_csv(decisions, report)
    print_summary(decisions)
    print(f"\n報表: {report}")

    reject_dir = args.reject_dir or folder / "rejected"
    moved = move_rejected(decisions, reject_dir, dry_run=args.dry_run)
    if moved:
        verb = "預計搬移" if args.dry_run else "已搬移"
        print(f"{verb} {len(moved)} 張到 {reject_dir}")
    return 0
