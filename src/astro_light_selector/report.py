"""輸出挑片報表。"""

from __future__ import annotations

import csv
from collections import Counter
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
    "iso", "gain", "camera",
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
                camera=row.get("camera") or None,
                **{k: float(row[k]) if row.get(k) else None for k in OPTIONAL_NUMBERS},
            ))
    return frames


def report_subfolders(path: Path, folder: Path) -> bool:
    """報表是子資料夾模式存的，而且那些子資料夾還在（重開時要包含子資料夾）。

    檔案後來被搬回同一層、子資料夾都不在了，就不算：不然報表一張都對不上，要整批重新量測。
    """
    if not path.is_file():
        return False
    with path.open(newline="", encoding="utf-8-sig") as fh:
        dirs = {row["file"].rpartition("/")[0] for row in csv.DictReader(fh) if "/" in row["file"]}
    return any((folder / d).is_dir() for d in dirs)


def match_frames(frames: list[FrameMetrics], files: list[Path], home_of: dict[str, str],
                 folder: Path) -> tuple[list[FrameMetrics], dict[str, str]]:
    """把報表讀回來的量測結果對到現在資料夾裡的檔案（files、home_of 是 collect_files 的結果）。

    先照 frame_key（相對路徑）比對；對不上的再用檔名比，只有檔名在兩邊剩下的都只有一個時才認
    （單眼的 DSC0001.ARW 每晚重複，重複的當成新檔案）。認回的片 file 改成現在的位置。
    回傳 (對得上的片, {舊代號: 新代號})，後者用來把手動覆寫換到新代號。
    """
    now = {frame_key(home_of.get(str(p), p), folder): Path(home_of.get(str(p), p)) for p in files}
    matched: list[FrameMetrics] = []
    rest: list[FrameMetrics] = []
    for f in frames:
        key = frame_key(f.file, folder)
        if key in now:
            matched.append(f)
            del now[key]
        else:
            rest.append(f)
    names_now = Counter(p.name for p in now.values())
    names_report = Counter(Path(f.file).name for f in rest)
    by_name = {p.name: (key, p) for key, p in now.items() if names_now[p.name] == 1}
    renamed: dict[str, str] = {}
    for f in rest:
        name = Path(f.file).name
        if names_report[name] == 1 and name in by_name:
            key, p = by_name[name]
            renamed[frame_key(f.file, folder)] = key
            f.file = str(p)
            matched.append(f)
    return matched, renamed


def format_duration(seconds: float) -> str:
    """總曝光時間，例如 16h 55m。"""
    if 0 < seconds < 60:
        return f"{seconds:.0f}s"
    h, m = divmod(int(round(seconds / 60)), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def kept_exposure(decisions: list[Decision]) -> tuple[float, int, int]:
    """保留片的總曝光秒數、保留張數、其中沒有曝光時間（沒算進去）的張數。"""
    kept = [d for d in decisions if d.keep]
    missing = sum(1 for d in kept if not d.metrics.exposure)
    return sum(d.metrics.exposure or 0 for d in kept), len(kept), missing


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
