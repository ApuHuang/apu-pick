"""讀取各廠牌相機的 RAW 檔（Canon、Nikon、Sony、Fujifilm、OM System / Olympus、Panasonic、Pentax、DNG…）。

用 rawpy（LibRaw）讀感光元件還沒解馬賽克的原始資料，扣掉黑位後當成 OSC 的 CFA 影像處理，
並把快門、ISO、拍攝時間、飽和值填進跟 FITS 一樣的 header 欄位，後面的量測流程不用分開寫。

- 一般 2×2 Bayer：header 的 BAYERPAT 設成排列（例如 RGGB），量測時做 2×2 super-pixel
- Fujifilm X-Trans（6×6）：設 CFAGRID = 3，量測時做 3×3 binning（每個 3×3 小區塊的 R/G/B 數量固定，合成後不會有格紋）
- 已經解過馬賽克的線性 DNG：直接取各色平均，不做 binning

拍攝時間是相機時鐘的本地時間（沒有時區），只用來排順序和切每一晚。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

RAW_SUFFIXES = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".pef", ".dng", ".srw"}


def _metadata(raw, path: Path) -> tuple[float | None, float | None, datetime | None]:
    """(快門秒數, ISO, 拍攝時間)。

    新版 rawpy 的 raw.other 所有格式都讀得到；舊版（Intel Mac 只有 0.25）沒有，退回用 exifread 讀 EXIF
    （CR3 / RAF 讀不到），再不行拍攝時間就用檔案時間。
    """
    shutter = iso = when = None
    other = getattr(raw, "other", None)
    if other is not None:
        shutter = float(other.shutter_speed) or None
        iso = float(other.iso_speed) or None
        when = other.timestamp if isinstance(other.timestamp, datetime) else None
    if shutter is None or when is None:
        try:
            import exifread

            with path.open("rb") as fh:
                tags = exifread.process_file(fh, details=False)
            if shutter is None and "EXIF ExposureTime" in tags:
                shutter = float(tags["EXIF ExposureTime"].values[0])
            if iso is None and "EXIF ISOSpeedRatings" in tags:
                iso = float(tags["EXIF ISOSpeedRatings"].values[0])
            stamp = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
            if when is None and stamp:
                when = datetime.strptime(str(stamp), "%Y:%m:%d %H:%M:%S")
        except Exception:  # noqa: BLE001  EXIF 讀不到不影響量測
            pass
    if when is None:
        when = datetime.fromtimestamp(os.path.getmtime(path))
    return shutter, iso, when


def load_raw(path: Path) -> tuple[np.ndarray, fits.Header]:
    """讀 RAW，回傳（扣掉黑位的 2D float32 CFA 影像, 跟 FITS 相同欄位的 header）。"""
    import rawpy

    header = fits.Header()
    with rawpy.imread(str(path)) as raw:
        data = raw.raw_image_visible.astype(np.float32)
        black = np.asarray(raw.black_level_per_channel, dtype=np.float32)
        pattern = raw.raw_pattern
        if data.ndim == 2 and pattern is not None:
            data -= black[raw.raw_colors_visible]
            desc = raw.color_desc.decode()
            if pattern.shape == (2, 2):
                header["BAYERPAT"] = "".join(desc[c] for c in pattern.flatten())
            else:
                header["CFAGRID"] = 3  # X-Trans
        else:
            # 已經解過馬賽克的線性 DNG
            data = (data - float(black.mean())).mean(axis=2) if data.ndim == 3 else data - float(black.mean())
        header["SATLEVEL"] = float(raw.white_level) - float(black.max())
        shutter, iso, when = _metadata(raw, path)
    if shutter:
        header["EXPTIME"] = shutter
    if iso:
        header["ISO"] = iso
    if when is not None:
        header["DATE-OBS"] = when.isoformat(timespec="seconds")
    return data, header
