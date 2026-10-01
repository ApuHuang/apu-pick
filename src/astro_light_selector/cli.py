"""命令列介面。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .grouping import DEFAULT_GROUP_KEYS, EXPOSURE_TOLERANCE, GROUP_KEYS, parse_group_keys
from .i18n import APP_NAME, APP_SUBTITLE, tr, tr_error
from .metrics import FrameMetrics
from .pipeline import (REJECT_DIR_NAME, REPORT_NAME, collect_files, decide, measure_files, move_files,
                       restore_files, should_recurse)
from .report import has_subfolders, print_score_summary, print_summary, read_csv, read_overrides, write_csv
from .scoring import METRICS, ScoreConfig
from .selector import Thresholds


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apu-pick",
        description=f"{APP_NAME}（{APP_SUBTITLE}）：量每張 light frame 的 FWHM、星點數、離心率、背景，"
                    "打分數後把淘汰的搬到 rejected 資料夾。",
    )
    p.add_argument("folder", type=Path, help="放 light frames 的資料夾")
    p.add_argument("--reject-dir", type=Path, default=None,
                   help="淘汰片搬去的資料夾（預設 <folder>/rejected；子資料夾模式不能指定）")
    p.add_argument("--recursive", action="store_true",
                   help="包含子資料夾，每張的淘汰片搬到自己所在資料夾的 rejected（folder 本身沒有影像、"
                        "子資料夾有時會自動打開）")
    p.add_argument("--report", type=Path, default=None,
                   help="CSV 報表路徑（預設 <folder>/selection_report.csv）")
    p.add_argument("--dry-run", action="store_true", help="只分析與輸出報表，不搬檔案")
    p.add_argument("--workers", type=int, default=1, help="平行處理數（預設 1）")
    p.add_argument("--from-report", type=Path, default=None,
                   help="不重新量測，直接讀之前的 CSV 報表重新套門檻（調參數用）")
    p.add_argument("--mode", choices=["score", "rules"], default="score",
                   help="score：以最佳範本評分（預設）；rules：各指標獨立門檻")
    p.add_argument("--group-by", type=str, default=",".join(DEFAULT_GROUP_KEYS),
                   help=f"依這些欄位分組、各組分開算門檻，逗號分隔（{', '.join(GROUP_KEYS)}；"
                        "gain 是 ISO 或增益，folder 是子資料夾），none 表示整批一起算"
                        f"（預設 {','.join(DEFAULT_GROUP_KEYS)}）")
    p.add_argument("--exposure-tolerance", type=float, default=EXPOSURE_TOLERANCE,
                   help=f"曝光時間差在幾秒以內算同一組（單眼 B 快門常有一兩秒誤差，預設 {EXPOSURE_TOLERANCE:g}）")
    p.add_argument("--plot", nargs="?", type=Path, const=True, default=None,
                   help="輸出指標趨勢圖 PNG（預設 <folder>/selection_plot.png，需要 matplotlib）")
    p.add_argument("--restore", action="store_true",
                   help="把 rejected 資料夾裡的檔案搬回原位後結束（可配合 --dry-run）")
    g = p.add_argument_group("評分模式（--mode score）")
    g.add_argument("--top-frac", type=float, default=0.10, help="每個指標取最好的前幾成當範本（預設 0.10）")
    g.add_argument("--pass-pct", type=float, default=0.80, help="及格線 = 範本分數 × 此值（預設 0.80）")
    g.add_argument("--margin-k", type=float, default=1.5, help="眾數往下容許幾個 MAD（預設 1.5）")
    g.add_argument("--weights", type=str, default=None,
                   help="權重，例如 fwhm=0.35,eccentricity=0.3,n_stars=0.2,background=0.15")
    direct = g.add_mutually_exclusive_group()
    direct.add_argument("--min-score", type=float, default=None,
                        help="直接指定門檻：分數低於此值就淘汰（0~100，取代自動門檻）")
    direct.add_argument("--keep-best", type=float, default=None,
                        help="只留能評分的影像中分數最高的這個比例，例如 0.7 = 前 70%%（每組各自算）")

    g = p.add_argument_group("規則模式（--mode rules）相對門檻（中位數 ± k×MAD）")
    g.add_argument("-k", type=float, default=None, help="一次設定下面四個 k（越小越嚴）")
    g.add_argument("--fwhm-k", type=float, default=None, help="預設 1.5")
    g.add_argument("--stars-k", type=float, default=None, help="預設 2.0")
    g.add_argument("--ecc-k", type=float, default=None, help="預設 1.5")
    g.add_argument("--background-k", type=float, default=None, help="預設 3.0")

    g = p.add_argument_group("規則模式（--mode rules）絕對門檻（設 -1 表示不使用）")
    g.add_argument("--max-fwhm", type=float, default=-1, help="FWHM 上限（像素）")
    g.add_argument("--min-stars", type=int, default=20, help="星點數下限")
    g.add_argument("--max-ecc", type=float, default=0.7, help="離心率上限")
    g.add_argument("--max-saturated", type=float, default=-1, help="飽和像素比例上限")
    return p


def _opt(v: float) -> float | None:
    return None if v < 0 else v


def _parse_weights(text: str | None) -> dict[str, float] | None:
    if not text:
        return None
    out: dict[str, float] = {}
    for item in text.split(","):
        key, _, val = item.partition("=")
        key = key.strip()
        if key not in METRICS:
            raise ValueError(f"不支援的權重指標: {key}（可用 {', '.join(METRICS)}）")
        try:
            out[key] = float(val)
        except ValueError:
            raise ValueError(f"權重格式錯誤: {item!r}，應為 指標=數字") from None
    if sum(out.values()) <= 0:
        raise ValueError("權重總和必須大於 0")
    return out


def _progress(i: int, n: int, m: FrameMetrics) -> None:
    name = Path(m.file).name
    if m.error:
        print(f"[{i}/{n}] {name}：錯誤 {tr_error(m.error)}")
    else:
        print(f"[{i}/{n}] {name}：星點 {m.n_stars}、FWHM {m.fwhm:.2f}、"
              f"離心率 {m.eccentricity:.2f}、背景 {m.background:.0f}")


def _build_thresholds(args: argparse.Namespace) -> Thresholds:
    defaults = Thresholds()

    def k_of(v: float | None, default: float) -> float:
        if v is not None:
            return v
        return args.k if args.k is not None else default

    return Thresholds(
        fwhm_k=k_of(args.fwhm_k, defaults.fwhm_k),
        stars_k=k_of(args.stars_k, defaults.stars_k),
        ecc_k=k_of(args.ecc_k, defaults.ecc_k),
        background_k=k_of(args.background_k, defaults.background_k),
        max_fwhm=_opt(args.max_fwhm),
        min_stars=None if args.min_stars < 0 else args.min_stars,
        max_eccentricity=_opt(args.max_ecc),
        max_saturated_frac=_opt(args.max_saturated),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    folder: Path = args.folder
    if not folder.is_dir():
        print(f"找不到資料夾: {folder}", file=sys.stderr)
        return 1
    report = args.report or folder / REPORT_NAME
    source = args.from_report or report
    recursive = args.recursive or has_subfolders(source) or should_recurse(folder)
    if recursive and args.reject_dir:
        parser.error("子資料夾模式下淘汰片固定搬到各自資料夾的 rejected，不能指定 --reject-dir")
    if recursive and not args.recursive:
        print("這個資料夾本身沒有影像（或上次是用子資料夾模式），自動包含子資料夾")
    reject_dir = args.reject_dir or folder / REJECT_DIR_NAME
    where = "各資料夾裡的 rejected" if recursive else str(reject_dir)

    if args.restore:
        restored = restore_files(folder, reject_dir, recursive, dry_run=args.dry_run)
        verb = "預計搬回" if args.dry_run else "已搬回"
        print(f"{verb} {len(restored)} 張")
        return 0

    try:
        group_keys = parse_group_keys(args.group_by)
        weights = _parse_weights(args.weights)
    except ValueError as exc:
        parser.error(str(exc))
    if args.mode != "score" and (args.min_score is not None or args.keep_best is not None):
        parser.error("--min-score / --keep-best 只能用在評分模式（--mode score）")
    if args.min_score is not None and not 0 <= args.min_score <= 100:
        parser.error("--min-score 要在 0~100 之間")
    if args.keep_best is not None and not 0 < args.keep_best <= 1:
        parser.error("--keep-best 要在 0~1 之間，例如 0.7")

    if args.from_report:
        if not args.from_report.is_file():
            print(f"找不到報表: {args.from_report}", file=sys.stderr)
            return 1
        frames = read_csv(args.from_report, folder)
        print(f"從 {args.from_report} 讀入 {len(frames)} 張的量測結果")
    else:
        files, home_of = collect_files(folder, reject_dir, recursive)
        if not files:
            print(f"{folder} 內沒有影像檔案", file=sys.stderr)
            return 1
        extra = f"（含之前淘汰的 {len(home_of)} 張）" if home_of else ""
        n_dirs = len({Path(home_of.get(str(f), f)).resolve().parent for f in files})
        dirs = f"，{n_dirs} 個資料夾" if recursive else ""
        print(f"分析 {len(files)} 張影像{extra}{dirs}...")
        frames = measure_files(files, args.workers, progress=_progress, home_of=home_of)

    cfg = ScoreConfig(top_frac=args.top_frac, pass_pct=args.pass_pct, margin_k=args.margin_k,
                      min_score=args.min_score, keep_best=args.keep_best)
    if weights:
        cfg.weights = weights
    th = _build_thresholds(args)

    # 視窗版在清單上手動覆寫的結果存在報表裡；重寫報表前先讀出來，照樣套用
    overrides = read_overrides(source)
    sel = decide(frames, group_keys, args.mode, cfg, th, args.exposure_tolerance, overrides, root=folder)
    for label, size in sel.group_sizes.items():
        if len(sel.group_sizes) > 1:
            print(f"\n== {label}（{size} 張）==")
        if args.mode == "score":
            result = sel.results[label]
            if result is None:
                print(tr("summary.no_measurable"))
            else:
                print_score_summary(result)
    decisions = sel.decisions

    write_csv(decisions, report, root=folder)
    print_summary(decisions, folder)
    print(f"\n報表: {report}")

    if args.plot:
        plot_path = folder / "selection_plot.png" if args.plot is True else args.plot
        try:
            from .plot import plot_decisions
            plot_decisions(decisions, plot_path, sel.thresholds)
            print(f"趨勢圖: {plot_path}")
        except ImportError:
            print("畫圖需要 matplotlib：pip install matplotlib", file=sys.stderr)

    moved_out, moved_back = move_files(decisions, reject_dir, recursive, dry_run=args.dry_run)
    if moved_out:
        verb = "預計搬移" if args.dry_run else "已搬移"
        print(f"{verb} {len(moved_out)} 張到 {where}")
    if moved_back:
        verb = "預計搬回" if args.dry_run else "已搬回"
        print(f"{verb} {len(moved_back)} 張這次變成保留的到原位")
    return 0
