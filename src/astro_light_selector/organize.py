"""依參數把影像搬進子資料夾整理，例如 ASI2600MC/Ha/300s/，整理過的可以全部搬回原位。

同一個資料夾混了多台相機、多個濾鏡、多種曝光時，先整理好再挑片、疊圖比較好管。
資料夾名稱是檔案系統上的資料，不跟著介面語言（跟報表欄位一樣維持英文）。
搬了哪些檔案記在開啟的資料夾裡的 organize_log.csv（相對路徑，整個資料夾換位置也還原得回去）。
"""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

from .grouping import EXPOSURE_TOLERANCE, assign_nights, exposure_buckets
from .metrics import FrameMetrics
from .pipeline import frame_key

ORGANIZE_KEYS = ("camera", "filter", "exposure", "gain", "night")
DEFAULT_ORGANIZE_KEYS = ("camera", "filter", "exposure")
ORGANIZE_LOG = "organize_log.csv"


def safe_name(text: str) -> str:
    """當資料夾名稱用：Windows 不能用的字元換成 _，頭尾的空白和點拿掉（. 開頭的資料夾挑片時會被跳過）。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return name or "_"


def folder_names(frames: list[FrameMetrics], keys: tuple[str, ...],
                 exposure_tolerance: float = EXPOSURE_TOLERANCE) -> dict[str, list[str]]:
    """每張要放進的資料夾（一層一層），{file: [相機, 濾鏡, …]}；順序照 ORGANIZE_KEYS。"""
    nights = assign_nights(frames) if "night" in keys else {}
    exposures = exposure_buckets(frames, exposure_tolerance) if "exposure" in keys else {}
    out: dict[str, list[str]] = {}
    for f in frames:
        parts = []
        for k in ORGANIZE_KEYS:
            if k not in keys:
                continue
            if k == "camera":
                parts.append(f.camera or "UnknownCamera")
            elif k == "filter":
                parts.append(f.filter or "NoFilter")
            elif k == "exposure":
                parts.append(exposures.get(f.exposure, "UnknownExposure") if f.exposure else "UnknownExposure")
            elif k == "gain":
                if f.iso is not None:
                    parts.append(f"ISO{f.iso:g}")
                elif f.gain is not None:
                    parts.append(f"Gain{f.gain:g}")
                else:
                    parts.append("UnknownGain")
            else:
                parts.append(nights.get(f.file) or "UnknownNight")
        out[f.file] = [safe_name(p) for p in parts]
    return out


def read_log(folder: Path) -> dict[Path, Path]:
    """{原位置: 目前位置}，都是絕對路徑。"""
    log = folder / ORGANIZE_LOG
    if not log.is_file():
        return {}
    root = folder.resolve()
    with log.open(newline="", encoding="utf-8-sig") as fh:
        return {(root / row["source"]).resolve(): (root / row["target"]).resolve() for row in csv.DictReader(fh)}


def write_log(folder: Path, entries: dict[Path, Path]) -> None:
    """整份重寫；一筆都沒有就刪掉紀錄檔。"""
    log = folder / ORGANIZE_LOG
    if not entries:
        log.unlink(missing_ok=True)
        return
    root = folder.resolve()
    with log.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "target"])
        for src, target in entries.items():
            w.writerow([src.relative_to(root).as_posix(), target.relative_to(root).as_posix()])


def plan_organize(frames: list[FrameMetrics], folder: Path, keys: tuple[str, ...],
                  exposure_tolerance: float = EXPOSURE_TOLERANCE) -> list[tuple[Path, Path]]:
    """要怎麼搬：[(目前位置, 新位置)]，已經在對的地方的不列。

    新位置 = folder/相機/濾鏡/…/原本的子資料夾/檔名。原本的子資料夾照留（各晚檔名常常重複）；
    整理過又重新整理時，從最早的原位置算，不會越疊越深。同名的檔案加 _1、_2，不覆蓋。
    """
    root = folder.resolve()
    original_of = {cur: src for src, cur in read_log(folder).items()}
    names = folder_names(frames, keys, exposure_tolerance)
    taken: set[Path] = set()
    plan: list[tuple[Path, Path]] = []
    for f in frames:
        cur = Path(f.file).resolve()
        if not cur.is_file():
            continue  # 已經被搬到淘汰片資料夾或被刪掉的不動
        orig = original_of.get(cur, cur)
        sub = frame_key(orig.parent, root)
        target_dir = root.joinpath(*names[f.file], *([] if sub == "." else sub.split("/")))
        target = target_dir / cur.name
        if target == cur:
            continue
        i = 1
        while target.exists() or target in taken:
            target = target_dir / f"{cur.stem}_{i}{cur.suffix}"
            i += 1
        taken.add(target)
        plan.append((cur, target))
    return plan


def _prune_up(start: Path, stop: Path) -> None:
    """從 start 往上把空掉的資料夾收掉，到 stop（不含）為止。"""
    d = start
    while d != stop and stop in d.parents:
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def organize(plan: list[tuple[Path, Path]], folder: Path) -> list[tuple[Path, Path]]:
    """照 plan_organize 的結果搬檔並記錄，回傳搬好的 [(原本位置, 新位置)]。中途出錯也會把搬好的記下來。"""
    root = folder.resolve()
    log = read_log(folder)
    original_of = {cur: src for src, cur in log.items()}
    done: list[tuple[Path, Path]] = []
    try:
        for cur, target in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(target))
            orig = original_of.pop(cur, cur)
            log.pop(orig, None)
            if target != orig:
                log[orig] = target
            done.append((cur, target))
            _prune_up(cur.parent, root)
    finally:
        if done:
            write_log(folder, log)
    return done


def undo_organize(folder: Path, dry_run: bool = False) -> list[tuple[Path, Path]]:
    """整理過的檔案全部搬回原位，回傳 [(目前位置, 原位置)]；原位已經有同名檔案的不覆蓋、留在原地。"""
    root = folder.resolve()
    log = read_log(folder)
    done: list[tuple[Path, Path]] = []
    try:
        for orig, cur in list(log.items()):
            if not cur.is_file() or orig.exists():
                continue
            if not dry_run:
                orig.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cur), str(orig))
                del log[orig]
                _prune_up(cur.parent, root)
            done.append((cur, orig))
    finally:
        if not dry_run and done:
            write_log(folder, log)
    return done


def pending_undo(folder: Path) -> int:
    """還可以搬回原位的張數。"""
    return sum(1 for orig, cur in read_log(folder).items() if cur.is_file() and not orig.exists())

