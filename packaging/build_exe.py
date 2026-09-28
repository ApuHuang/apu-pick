"""打包成 Windows 執行檔：dist/AstroLightSelector/AstroLightSelector.exe，再壓成 zip 方便分享。

    pip install -e .[exe]
    python packaging/build_exe.py

打包完會用合成星場實際跑一次 exe，確認量測（含多核心）與畫圖都正常才算成功。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from astro_light_selector import __version__  # noqa: E402
from tests.synth import make_session  # noqa: E402

NAME = "AstroLightSelector"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON = ROOT / "src" / "astro_light_selector" / "assets" / "app.ico"
# 用不到但環境裡可能有、會被順手打包進去的大套件
EXCLUDES = ["pytest", "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6", "notebook", "sphinx"]


def build() -> Path:
    import PyInstaller.__main__

    args = [
        str(ROOT / "packaging" / "gui_entry.py"),
        "--name", NAME,
        "--windowed",  # 不要跳出黑色主控台視窗
        "--noconfirm", "--clean",
        "--icon", str(ICON),
        "--paths", str(ROOT / "src"),
        "--add-data", f"{ICON}{os.pathsep}astro_light_selector/assets",
        "--collect-submodules", "photutils",
        "--copy-metadata", "photutils",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    PyInstaller.__main__.run(args)
    return DIST / NAME / f"{NAME}.exe"


def smoke_test(exe: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "lights"
        files = make_session(folder)
        n = len(files["good"]) + len(files["bad"])
        out = Path(tmp) / "smoke.txt"
        proc = subprocess.run([str(exe), "--smoke-test", str(folder), str(out)], timeout=600)
        text = out.read_text(encoding="utf-8") if out.exists() else "(沒有輸出)"
        if proc.returncode != 0 or not text.startswith(f"ok frames={n} "):
            raise SystemExit(f"exe 測試失敗（exit {proc.returncode}）：\n{text}")
        if not out.with_suffix(".png").is_file():
            raise SystemExit("exe 測試失敗：沒有畫出趨勢圖")
        print(f"exe 測試通過：{text.strip()}")


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> None:
    exe = build()
    smoke_test(exe)
    archive = shutil.make_archive(str(DIST / f"{NAME}-{__version__}-win64"), "zip", root_dir=DIST, base_dir=NAME)
    print(f"\n執行檔：{exe}（整個資料夾 {folder_size(exe.parent) / 2**20:.0f} MB）")
    print(f"分享用：{archive}（{Path(archive).stat().st_size / 2**20:.0f} MB）")


if __name__ == "__main__":
    main()
