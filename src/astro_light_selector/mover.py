"""依挑片結果搬移檔案：reject 的搬進 reject 資料夾，之前搬走但現在變成 keep 的搬回原位。"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

from .metrics import FITS_SUFFIXES
from .selector import Decision

# 記錄程式搬了哪些檔案（原位置 -> 目前位置）。重跑時靠它把之前 reject 的也算進同一批、
# 變成 keep 的搬回原位；--restore 也靠它還原（檔名被加過 _1 也能還原）。
# 沒有紀錄的檔案（使用者自己手動丟進來的）程式不會動。
MOVE_LOG = "moves.csv"


def read_move_log(reject_dir: Path) -> dict[Path, Path]:
    """回傳 {原位置: 在 reject_dir 裡的位置}，路徑都是絕對路徑。"""
    log = reject_dir / MOVE_LOG
    if not log.exists():
        return {}
    with log.open(newline="", encoding="utf-8-sig") as fh:
        return {Path(row["source"]).resolve(): Path(row["target"]).resolve() for row in csv.DictReader(fh)}


def write_move_log(reject_dir: Path, entries: dict[Path, Path]) -> None:
    """整份重寫，只留檔案還在 reject_dir 的紀錄；一筆都沒有就刪掉紀錄檔。"""
    entries = {src: target for src, target in entries.items() if target.exists()}
    log = reject_dir / MOVE_LOG
    if not entries:
        log.unlink(missing_ok=True)
        return
    with log.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "target"])
        for src, target in entries.items():
            w.writerow([str(src), str(target)])


def moved_away(reject_dir: Path, folder: Path) -> dict[Path, Path]:
    """之前從 folder 搬走、現在還在 reject_dir 的檔案：{原位置: 目前位置}。"""
    folder = folder.resolve()
    return {src: target for src, target in read_move_log(reject_dir).items()
            if src.parent == folder and target.exists() and not src.exists()}


def _remove_if_empty(folder: Path) -> None:
    """全部搬回去之後，把空的 reject 資料夾收掉（不是空的 rmdir 會失敗，不會刪到東西）。"""
    try:
        folder.rmdir()
    except OSError:
        pass


def _free_name(dest: Path, src: Path, taken: set[Path]) -> Path:
    """dest 裡不會覆蓋到別的檔案的名字，同名就加 _1、_2…"""
    target = dest / src.name
    i = 1
    while target.exists() or target in taken:
        target = dest / f"{src.stem}_{i}{src.suffix}"
        i += 1
    taken.add(target)
    return target


def sync_files(decisions: list[Decision], reject_dir: Path,
               dry_run: bool = False) -> tuple[list[Path], list[Path]]:
    """讓檔案位置跟挑片結果一致。

    - reject、還在原位的 → 搬進 reject_dir
    - keep、之前被程式搬進 reject_dir 的 → 搬回原位

    回傳 (這次搬進 reject_dir 的新位置, 這次搬回原位的路徑)；dry_run 時回傳預計的結果、不動檔案。
    檔案不在原位也沒有搬移紀錄的（例如使用者自己刪了）會略過。
    """
    log = read_move_log(reject_dir)
    moved_out: list[Path] = []
    moved_back: list[Path] = []
    taken: set[Path] = set()
    try:
        for d in decisions:
            home = Path(d.metrics.file).resolve()
            if d.keep:
                cur = log.get(home)
                if cur is None or not cur.exists() or home.exists():
                    continue
                if not dry_run:
                    shutil.move(str(cur), str(home))
                    del log[home]
                moved_back.append(home)
            elif home.exists():
                target = _free_name(reject_dir, home, taken)
                if not dry_run:
                    reject_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(home), str(target))
                    log[home] = target.resolve()
                moved_out.append(target)
    finally:
        # 中途出錯也要把已經搬完的記下來，下次才搬得回來
        if not dry_run and (moved_out or moved_back):
            write_move_log(reject_dir, log)
            _remove_if_empty(reject_dir)
    return moved_out, moved_back


def restore_rejected(reject_dir: Path, folder: Path, dry_run: bool = False) -> list[Path]:
    """把 reject_dir 裡的檔案全部搬回原位，回傳搬回後（或 dry-run 預計）的路徑清單。

    有搬移紀錄的照紀錄還原；沒有紀錄的 FITS（舊版搬的或手動丟進來的）直接搬回 folder。
    原位已經有同名檔案的不覆蓋，留在 reject_dir。
    """
    log = read_move_log(reject_dir)
    pairs = [(target, src) for src, target in log.items()]  # (目前位置, 要搬回的位置)
    logged = set(log.values())
    if reject_dir.is_dir():
        for p in sorted(reject_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in FITS_SUFFIXES and p.resolve() not in logged:
                pairs.append((p, folder / p.name))

    restored: list[Path] = []
    for cur, home in pairs:
        if not cur.exists() or home.exists():
            continue
        if not dry_run:
            home.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(home))
        restored.append(home)

    if not dry_run:
        if log:
            write_move_log(reject_dir, log)
        _remove_if_empty(reject_dir)
    return restored
