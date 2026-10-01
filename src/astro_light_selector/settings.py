"""使用者設定（目前只有介面語言），存在系統的應用程式設定資料夾。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 設定資料夾的名稱（畫面上顯示的名稱在 i18n.APP_NAME）；整合版會改成 APU Astro 底下的一個區段
APP_NAME = "APU Pick"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**values: object) -> None:
    """合併寫入；存不進去就算了，只是下次要重選。"""
    data = load_settings()
    data.update(values)
    try:
        settings_path().parent.mkdir(parents=True, exist_ok=True)
        settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
