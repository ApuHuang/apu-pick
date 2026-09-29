"""打包成可直接執行的程式，再壓成 zip 方便分享。

    pip install -e .[exe]
    python packaging/build_exe.py

- Windows：dist/APUPick/APUPick.exe → dist/APUPick-<版本>-win64.zip
- macOS：  dist/APUPick.app          → dist/APUPick-<版本>-macos-<arm64|x86_64>.zip
  （PyInstaller 不能跨平台，Mac 版要在 Mac 上打包，平常由 GitHub Actions 的雲端 Mac 負責）

打包完會用合成星場實際跑一次打包好的程式，確認量測（含多核心）與畫圖都正常才算成功。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from astro_light_selector import __version__  # noqa: E402
from tests.synth import make_session  # noqa: E402

NAME = "APUPick"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "src" / "astro_light_selector" / "assets"
IS_MAC = sys.platform == "darwin"
# 用不到但環境裡可能有、會被順手打包進去的大套件
EXCLUDES = ["pytest", "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6", "notebook", "sphinx"]


def platform_tag() -> str:
    if sys.platform == "win32":
        return "win64"
    if IS_MAC:
        return f"macos-{platform.machine()}"
    return f"{sys.platform}-{platform.machine()}"


def build() -> Path:
    """回傳打包好的執行檔路徑。"""
    import PyInstaller.__main__

    args = [
        str(ROOT / "packaging" / "gui_entry.py"),
        "--name", NAME,
        "--windowed",  # Windows 不跳黑色主控台；Mac 產生 .app
        "--noconfirm", "--clean",
        "--icon", str(ASSETS / "app.ico"),  # Mac 上會自動轉成 .icns
        "--paths", str(ROOT / "src"),
        "--add-data", f"{ASSETS}{os.pathsep}astro_light_selector/assets",
        "--collect-submodules", "photutils",
        "--collect-all", "rawpy",  # 相機 RAW：rawpy 內含 LibRaw 的動態函式庫
        "--collect-data", "astropy_iers_data",  # 算仰角用的地球自轉資料（不連網）
        "--copy-metadata", "photutils",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ]
    if IS_MAC:
        args += ["--osx-bundle-identifier", "tw.apu-astrophotography.apu-pick"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    PyInstaller.__main__.run(args)
    if IS_MAC:
        return DIST / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return DIST / NAME / f"{NAME}.exe"


def smoke_test(exe: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "lights"
        files = make_session(folder)
        n = len(files["good"]) + len(files["bad"])
        out = Path(tmp) / "smoke.txt"
        proc = subprocess.run([str(exe), "--smoke-test", str(folder), str(out)], timeout=900)
        text = out.read_text(encoding="utf-8") if out.exists() else "(沒有輸出)"
        if proc.returncode != 0 or not text.startswith(f"ok frames={n} "):
            raise SystemExit(f"打包好的程式測試失敗（exit {proc.returncode}）：\n{text}")
        if not out.with_suffix(".png").is_file():
            raise SystemExit("打包好的程式測試失敗：沒有畫出趨勢圖")
        print(f"打包好的程式測試通過：{text.strip()}")


def archive() -> tuple[Path, Path]:
    """回傳 (打包好的資料夾或 .app, zip)。"""
    base = DIST / f"{NAME}-{__version__}-{platform_tag()}"
    if IS_MAC:
        app = DIST / f"{NAME}.app"
        # 用 ditto 壓縮才會保留 .app 裡的符號連結和執行權限
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), f"{base}.zip"],
                       check=True)
        return app, Path(f"{base}.zip")
    return DIST / NAME, Path(shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir=NAME))


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def main() -> None:
    exe = build()
    smoke_test(exe)
    bundle, zip_path = archive()
    print(f"\n程式：{bundle}（{folder_size(bundle) / 2**20:.0f} MB）")
    print(f"分享用：{zip_path}（{zip_path.stat().st_size / 2**20:.0f} MB）")


if __name__ == "__main__":
    main()
