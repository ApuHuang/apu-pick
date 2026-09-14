"""把 reject 的檔案搬到另一個資料夾。"""

from __future__ import annotations

import shutil
from pathlib import Path

from .selector import Decision


def move_rejected(decisions: list[Decision], dest: Path, dry_run: bool = False) -> list[Path]:
    """搬移 reject 的檔案到 dest，回傳搬移後（或 dry-run 預計）的路徑清單。"""
    targets = [Path(d.metrics.file) for d in decisions if not d.keep]
    if not targets:
        return []
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    moved: list[Path] = []
    for src in targets:
        target = dest / src.name
        # 避免覆蓋同名檔案
        i = 1
        while target.exists():
            target = dest / f"{src.stem}_{i}{src.suffix}"
            i += 1
        if not dry_run:
            shutil.move(str(src), str(target))
        moved.append(target)
    return moved
