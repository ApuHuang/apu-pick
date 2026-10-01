"""把一批影像依濾鏡 / 曝光 / ISO 或增益 / 夜晚 / 子資料夾分組，每組各自算門檻。

不同濾鏡（L 跟 Ha）或不同曝光時間的背景、星點數差好幾倍，混在一起算中位數，
會把整組窄頻或短曝光的全部 reject。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .i18n import tr
from .metrics import FrameMetrics

GROUP_KEYS = ("filter", "exposure", "gain", "night", "folder")
DEFAULT_GROUP_KEYS = ("filter", "exposure", "gain")
# 相鄰兩張間隔超過這麼久就當成不同晚：白天至少十幾小時，一晚中間被雲擋幾小時也還算同一晚
NIGHT_GAP = timedelta(hours=8)


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


# 曝光時間差在幾秒以內算同一組：單眼 B 快門計時常有一兩秒誤差（例如 301、302 秒）
EXPOSURE_TOLERANCE = 2.0


def exposure_buckets(frames: list[FrameMetrics], tolerance: float = EXPOSURE_TOLERANCE) -> dict[float, str]:
    """把曝光時間分群：由小到大排，跟這群第一個值相差在 tolerance 秒以內的歸同一群。

    回傳 {曝光秒數: 標籤}；群裡只有一種秒數顯示「300s」，混了幾種顯示「301~302s」。
    """
    values = sorted({f.exposure for f in frames if f.exposure is not None})
    clusters: list[list[float]] = []
    for v in values:
        if clusters and v - clusters[-1][0] <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    labels = {}
    for c in clusters:
        label = _fmt_exposure(c[0]) if len(c) == 1 else f"{c[0]:g}~{c[-1]:g}s"
        for v in c:
            labels[v] = label
    return labels


def _gain_label(f: FrameMetrics) -> str:
    """單眼看 ISO、天文相機看 GAIN；不同 ISO / 增益的背景、雜訊、星點數差很多。"""
    if f.iso is not None:
        return tr("group.iso", value=f"{f.iso:g}")
    if f.gain is not None:
        return tr("group.gain", value=f"{f.gain:g}")
    return tr("group.no_gain")


def _folder_of(f: FrameMetrics, root: Path | None) -> str:
    """影像所在的子資料夾（相對於開啟的資料夾）；就在開啟的資料夾裡是空字串。"""
    parent = Path(f.file).parent
    if root is not None:
        for a, b in ((parent, Path(root)), (parent.resolve(), Path(root).resolve())):
            try:
                rel = a.relative_to(b).as_posix()
                return "" if rel == "." else rel
            except ValueError:
                pass
    return parent.name


def group_frames(frames: list[FrameMetrics], keys: tuple[str, ...],
                 exposure_tolerance: float = EXPOSURE_TOLERANCE,
                 root: Path | None = None) -> dict[str, list[FrameMetrics]]:
    """回傳 {分組標籤: 影像清單}，保留原本順序。keys 為空時整批一組（標籤為空字串）。

    標籤用目前的介面語言組成，例如「濾鏡 L／曝光 300s」。
    ISO / 增益整批都沒有資料（例如舊報表）、或全部在同一個資料夾時，標籤不加那一段。
    root 是開啟的資料夾，依子資料夾分組時用。
    """
    if not keys:
        return {"": list(frames)}
    nights = assign_nights(frames) if "night" in keys else {}
    exposures = exposure_buckets(frames, exposure_tolerance) if "exposure" in keys else {}
    use_gain = "gain" in keys and any(f.iso is not None or f.gain is not None for f in frames)
    folders = {f.file: _folder_of(f, root) for f in frames} if "folder" in keys else {}
    use_folder = len(set(folders.values())) > 1
    groups: dict[str, list[FrameMetrics]] = {}
    for f in frames:
        parts = []
        for k in keys:
            if k == "filter":
                parts.append(tr("group.filter", value=f.filter) if f.filter else tr("group.no_filter"))
            elif k == "exposure":
                parts.append(tr("group.exposure", value=exposures.get(f.exposure, _fmt_exposure(f.exposure))))
            elif k == "gain":
                if use_gain:
                    parts.append(_gain_label(f))
            elif k == "folder":
                if use_folder:
                    folder = folders[f.file]
                    parts.append(tr("group.folder", value=folder) if folder else tr("group.top_folder"))
            else:
                parts.append(tr("group.night", value=nights.get(f.file, "?")))
        groups.setdefault(tr("group.sep").join(parts), []).append(f)
    return groups
