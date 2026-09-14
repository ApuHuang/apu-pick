"""輸出挑片報表。"""

from __future__ import annotations

import csv
from pathlib import Path

from .selector import Decision

COLUMNS = [
    "file", "keep", "reasons", "n_stars", "fwhm", "eccentricity",
    "background", "noise", "snr", "saturated_frac",
    "exposure", "filter", "date_obs", "error",
]
FLOAT_COLUMNS = ("fwhm", "eccentricity", "background", "noise", "snr", "saturated_frac")


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
            row["reasons"] = "; ".join(d.reasons)
            for key in FLOAT_COLUMNS:
                v = row[key]
                row[key] = "" if v is None or v != v else f"{v:.4f}"
            writer.writerow(row)


def print_summary(decisions: list[Decision]) -> None:
    kept = [d for d in decisions if d.keep]
    rejected = [d for d in decisions if not d.keep]
    print(f"\n共 {len(decisions)} 張，keep {len(kept)}，reject {len(rejected)}")
    if rejected:
        print("\nReject 清單：")
        for d in rejected:
            print(f"  {Path(d.metrics.file).name}: {'; '.join(d.reasons)}")
