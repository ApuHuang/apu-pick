"""輸出挑片報表。"""

from __future__ import annotations

import csv
from pathlib import Path

from .i18n import normalize_error, tr, tr_metric
from .metrics import FrameMetrics
from .pipeline import frame_key
from .scoring import ScoreResult
from .selector import Decision

COLUMNS = [
    "file", "keep", "score", "reasons", "n_stars", "fwhm", "eccentricity",
    "background", "noise", "snr", "saturated_frac",
    "exposure", "filter", "date_obs", "group", "error", "override",
    "altitude", "moon_alt", "moon_sep", "moon_illum",
    "iso", "gain",
]
FLOAT_COLUMNS = ("fwhm", "eccentricity", "background", "noise", "snr", "saturated_frac",
                 "altitude", "moon_alt", "moon_sep", "moon_illum")
# 之後版本加的數字欄位；舊報表沒有就是 None
OPTIONAL_NUMBERS = ("altitude", "moon_alt", "moon_sep", "moon_illum", "iso", "gain")


def read_csv(path: Path, folder: Path) -> list[FrameMetrics]:
    """讀回之前輸出的報表，讓 CLI 可以不重新量測就重套門檻。

    file 欄是相對於 folder 的路徑（子資料夾用 / 分隔；只有一層時就是檔名），會接回 folder。
    """
    frames: list[FrameMetrics] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            def f(key: str) -> float:
                return float(row[key]) if row[key] else float("nan")
            frames.append(FrameMetrics(
                file=str(folder / row["file"]),
                n_stars=int(row["n_stars"] or 0),
                fwhm=f("fwhm"), eccentricity=f("eccentricity"),
                background=f("background"), noise=f("noise"), snr=f("snr"),
                saturated_frac=f("saturated_frac"),
                exposure=float(row["exposure"]) if row["exposure"] else None,
                filter=row["filter"] or None,
                date_obs=row["date_obs"] or None,
                error=normalize_error(row["error"]) if row["error"] else None,
                **{k: float(row[k]) if row.get(k) else None for k in OPTIONAL_NUMBERS},
            ))
    return frames


def has_subfolders(path: Path) -> bool:
    """報表是用子資料夾模式存的（file 欄有子資料夾路徑）。"""
    if not path.is_file():
        return False
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return any("/" in row["file"] for row in csv.DictReader(fh))


def read_overrides(path: Path) -> dict[str, str]:
    """讀報表裡使用者的手動覆寫：{frame_key: "keep" / "reject"}；沒有報表或舊版報表沒有這欄就是空的。"""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return {row["file"]: row["override"] for row in csv.DictReader(fh)
                if row.get("override") in ("keep", "reject")}


def write_csv(decisions: list[Decision], path: Path, root: Path | None = None) -> None:
    """root 是開啟的資料夾：file 欄寫相對於它的路徑；沒給就用報表所在的資料夾。"""
    root = path.parent if root is None else root
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig 讓 Excel 直接開也不會亂碼
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for d in decisions:
            row = d.metrics.to_dict()
            row["file"] = frame_key(row["file"], root)
            row["keep"] = "keep" if d.keep else "reject"
            row["score"] = "" if d.score is None else f"{d.score:.1f}"
            row["reasons"] = "; ".join(str(r) for r in d.reasons)
            row["group"] = d.group
            row["override"] = d.override or ""
            for key in FLOAT_COLUMNS:
                v = row[key]
                row[key] = "" if v is None or v != v else f"{v:.4f}"
            for key in ("iso", "gain"):
                row[key] = "" if row[key] is None else f"{row[key]:g}"
            writer.writerow(row)


def score_summary_lines(result: ScoreResult) -> list[str]:
    """評分結果的說明（範本、眾數、門檻怎麼來的），用目前的介面語言。"""
    digits = {"fwhm": ".2f", "eccentricity": ".2f", "n_stars": ".0f", "background": ".0f", "snr": ".1f"}
    parts = [f"{tr_metric(m)} {v:{digits[m]}}" for m, v in result.reference.items()]
    if result.method == "keep_best":
        how = tr("summary.how.keep_best", n=sum(s >= result.threshold for s in result.scores.values()))
    else:
        how = tr(f"summary.how.{result.method}")
    return [
        tr("summary.reference", parts=tr("summary.part_sep").join(parts)),
        tr("summary.stats", mode=result.mode, mad=result.mad, pass_line=result.pass_line),
        tr("summary.threshold", threshold=result.threshold, how=how),
    ]


def print_score_summary(result: ScoreResult) -> None:
    print()
    for line in score_summary_lines(result):
        print(line)


def print_summary(decisions: list[Decision], root: Path | None = None) -> None:
    """root 是開啟的資料夾：子資料夾模式下印相對路徑，每晚同名的檔案才分得開。"""
    kept = [d for d in decisions if d.keep]
    rejected = [d for d in decisions if not d.keep]
    print()
    print(tr("summary.total", n=len(decisions), keep=len(kept), reject=len(rejected)))
    if rejected:
        print()
        print(tr("summary.reject_list"))
        for d in sorted(rejected, key=lambda d: (d.score is None, d.score or 0)):
            name = frame_key(d.metrics.file, root) if root is not None else Path(d.metrics.file).name
            print(f"  {name}: {'; '.join(str(r) for r in d.reasons)}")
