"""把一批影像依濾鏡 / 曝光 / 夜晚分組，每組各自算門檻。

不同濾鏡（L 跟 Ha）或不同曝光時間的背景、星點數差好幾倍，混在一起算中位數，
會把整組窄頻或短曝光的全部 reject。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .i18n import tr
from .metrics import FrameMetrics

GROUP_KEYS = ("filter", "exposure", "night")
# 相鄰兩張間隔超過這麼久就當成不同晚
NIGHT_GAP = timedelta(hours=4)


def parse_group_keys(text: str) -> tuple[str, ...]:
    if text.strip().lower() in ("", "none"):
        return ()
    keys = tuple(k.strip() for k in text.split(",") if k.strip())
    bad = [k for k in keys if k not in GROUP_KEYS]
    if bad:
        raise ValueError(f"不支援的分組欄位: {', '.join(bad)}（可用 {', '.join(GROUP_KEYS)}）")
    return keys


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().rstrip("Z"))
    except ValueError:
        return None


def assign_nights(frames: list[FrameMetrics]) -> dict[str, str]:
    """依 DATE-OBS 的時間間隔切出每一晚，回傳 file -> 該晚第一張的日期。

    不用日期直接切：DATE-OBS 是 UTC，一晚常常跨過 UTC 的午夜（或在亞洲是跨過當地午夜）。
    """
    timed = sorted(((t, f.file) for f in frames if (t := _parse_time(f.date_obs))), key=lambda x: x[0])
    nights: dict[str, str] = {}
    label, prev = "", None
    for t, file in timed:
        if prev is None or t - prev > NIGHT_GAP:
            label = t.date().isoformat()
        nights[file] = label
        prev = t
    return nights


def _fmt_exposure(v: float | None) -> str:
    if v is None:
        return "?"
    return f"{v:g}s"


def group_frames(frames: list[FrameMetrics], keys: tuple[str, ...]) -> dict[str, list[FrameMetrics]]:
    """回傳 {分組標籤: 影像清單}，保留原本順序。keys 為空時整批一組（標籤為空字串）。

    標籤用目前的介面語言組成，例如「濾鏡 L／曝光 300s」。
    """
    if not keys:
        return {"": list(frames)}
    nights = assign_nights(frames) if "night" in keys else {}
    groups: dict[str, list[FrameMetrics]] = {}
    for f in frames:
        parts = []
        for k in keys:
            if k == "filter":
                parts.append(tr("group.filter", value=f.filter) if f.filter else tr("group.no_filter"))
            elif k == "exposure":
                parts.append(tr("group.exposure", value=_fmt_exposure(f.exposure)))
            else:
                parts.append(tr("group.night", value=nights.get(f.file, "?")))
        groups.setdefault(tr("group.sep").join(parts), []).append(f)
    return groups
