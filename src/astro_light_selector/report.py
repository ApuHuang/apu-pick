"""輸出挑片報表。"""

from __future__ import annotations

import csv
from pathlib import Path

from .metrics import FrameMetrics
from .scoring import ScoreResult
from .selector import Decision

COLUMNS = [
    "file", "keep", "score", "reasons", "n_stars", "fwhm", "eccentricity",
    "background", "noise", "snr", "saturated_frac",
    "exposure", "filter", "date_obs", "group", "error",
]
FLOAT_COLUMNS = ("fwhm", "eccentricity", "background", "noise", "snr", "saturated_frac")


def read_csv(path: Path, folder: Path) -> list[FrameMetrics]:
    """讀回之前輸出的報表，讓 CLI 可以不重新量測就重套門檻。檔名會接回 folder。"""
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
                error=row["error"] or None,
            ))
    return frames


def write_csv(decisions: list[Decision], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig 讓 Excel 直接開也不會亂碼
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for d in decisions:
            row = d.metrics.to_dict()
            row["file"] = Path(row["file"]).name
            row["keep"] = "keep" if d.keep else "reject"
            row["score"] = "" if d.score is None else f"{d.score:.1f}"
            row["reasons"] = "; ".join(d.reasons)
            row["group"] = d.group
            for key in FLOAT_COLUMNS:
                v = row[key]
                row[key] = "" if v is None or v != v else f"{v:.4f}"
            writer.writerow(row)


def score_summary_lines(result: ScoreResult) -> list[str]:
    labels = {"fwhm": ("FWHM", ".2f"), "eccentricity": ("ecc", ".2f"),
              "n_stars": ("stars", ".0f"), "background": ("bkg", ".0f")}
    parts = [f"{labels[m][0]} {v:{labels[m][1]}}" for m, v in result.reference.items()]
    how = {
        "mode": "由眾數決定",
        "pass_line": "被及格線頂住",
        "min_score": "指定最低分數",
        "keep_best": f"只留前段 {sum(s >= result.threshold for s in result.scores.values())} 張",
    }[result.method]
    return [
        f"範本（各指標前段平均）: {'  '.join(parts)}",
        f"分數眾數 {result.mode:.1f}，MAD {result.mad:.1f}，及格線 {result.pass_line:.0f}",
        f"keep 門檻 = {result.threshold:.1f}（{how}）",
    ]


def print_score_summary(result: ScoreResult) -> None:
    print()
    for line in score_summary_lines(result):
        print(line)


def print_summary(decisions: list[Decision]) -> None:
    kept = [d for d in decisions if d.keep]
    rejected = [d for d in decisions if not d.keep]
    print(f"\n共 {len(decisions)} 張，keep {len(kept)}，reject {len(rejected)}")
    if rejected:
        print("\nReject 清單（分數低到高）：")
        for d in sorted(rejected, key=lambda d: (d.score is None, d.score or 0)):
            print(f"  {Path(d.metrics.file).name}: {'; '.join(d.reasons)}")
