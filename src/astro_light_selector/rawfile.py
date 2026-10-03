"""讀取各廠牌相機的 RAW 檔（Canon、Nikon、Sony、Fujifilm、OM System / Olympus、Panasonic、Pentax、DNG…）。

用 rawpy（LibRaw）讀感光元件還沒解馬賽克的原始資料，扣掉黑位後當成 OSC 的 CFA 影像處理，
並把快門、ISO、拍攝時間、飽和值填進跟 FITS 一樣的 header 欄位，後面的量測流程不用分開寫。

- 一般 2×2 Bayer：header 的 BAYERPAT 設成排列（例如 RGGB），量測時做 2×2 super-pixel
- Fujifilm X-Trans（6×6）：設 CFAGRID = 3，量測時做 3×3 binning（每個 3×3 小區塊的 R/G/B 數量固定，合成後不會有格紋）
- 已經解過馬賽克的線性 DNG：直接取各色平均，不做 binning

拍攝時間是相機時鐘的本地時間（沒有時區），只用來排順序和切每一晚。
"""

from __future__ import annotations

import logging
import os
import struct
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

RAW_SUFFIXES = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".pef", ".dng", ".srw"}


def _metadata(raw, path: Path) -> tuple[float | None, float | None, datetime | None]:
    """(快門秒數, ISO, 拍攝時間)。

    新版 rawpy 的 raw.other 所有格式都讀得到；舊版（Intel Mac 只有 0.25）沒有，退回讀 EXIF：
    CR3 讀它自己的 CMT2，其他用 exifread（RAF 讀不到），再不行拍攝時間就用檔案時間。
    """
    shutter = iso = when = None
    other = getattr(raw, "other", None)
    if other is not None:
        shutter = float(other.shutter_speed) or None
        iso = float(other.iso_speed) or None
        when = other.timestamp if isinstance(other.timestamp, datetime) else None
    if (shutter is None or when is None) and path.suffix.lower() == ".cr3":
        exif = _tiff_tags(_cr3_block(path, b"CMT2"), (0x829A, 0x8827, 0x9003))
        shutter = shutter or exif.get(0x829A) or None
        iso = iso or exif.get(0x8827) or None
        if when is None and exif.get(0x9003):
            try:
                when = datetime.strptime(exif[0x9003], "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass
    if (shutter is None or when is None) and path.suffix.lower() != ".cr3":
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


def _raf_camera(path: Path) -> str | None:
    """Fujifilm RAF：EXIF 讀不到，但檔頭固定位置有型號（"FUJIFILMCCD-RAW " 之後，第 28 個位元組起 32 個位元組）。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(60)
    except OSError:
        return None
    if not head.startswith(b"FUJIFILMCCD-RAW"):
        return None
    model = head[28:60].split(b"\0")[0].decode("ascii", "ignore").strip()
    return f"FUJIFILM {model}" if model else None


# CR3 的 moov 裡放 Canon 中繼資料的 uuid box
_CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")


def _boxes(data: bytes, start: int, end: int):
    """ISO 媒體格式（CR3）的 box：一個一個回傳 (類型, 內容開始, 結束)。"""
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", data[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        if size < head:
            return
        yield kind, i + head, min(i + size, end)
        i += size


def _tiff_tags(tiff: bytes, wanted: tuple[int, ...]) -> dict[int, str | float]:
    """一小段 TIFF 第一個 IFD 裡的幾個欄位：文字回傳 str，SHORT / LONG / RATIONAL 回傳數字；讀不到的不列。"""
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    found: dict[int, str | float] = {}
    if order is None:
        return found
    try:
        (ifd,) = struct.unpack(order + "I", tiff[4:8])
        (count,) = struct.unpack(order + "H", tiff[ifd:ifd + 2])
        for k in range(count):
            entry = ifd + 2 + 12 * k
            tag, kind, n, value = struct.unpack(order + "HHII", tiff[entry:entry + 12])
            if tag not in wanted:
                continue
            if kind == 2:  # ASCII：4 個位元組以內直接放在欄位裡
                raw = tiff[entry + 8:entry + 8 + n] if n <= 4 else tiff[value:value + n]
                found[tag] = raw.split(b"\0")[0].decode("ascii", "ignore").strip()
            elif kind == 3:  # SHORT
                found[tag] = float(struct.unpack(order + "H", tiff[entry + 8:entry + 10])[0])
            elif kind == 4:  # LONG
                found[tag] = float(value)
            elif kind == 5:  # RATIONAL
                num, den = struct.unpack(order + "II", tiff[value:value + 8])
                if den:
                    found[tag] = num / den
    except struct.error:
        pass
    return found


def _cr3_block(path: Path, name: bytes) -> bytes:
    """Canon CR3 的中繼資料：moov → Canon uuid → CMT1（相機型號）／CMT2（EXIF），每一塊都是一小段 TIFF。"""
    try:
        with path.open("rb") as fh:
            data = fh.read(256 * 1024)  # moov 在檔案最前面，通常幾十 KB
        for kind, start, end in _boxes(data, 0, len(data)):
            if kind != b"moov":
                continue
            for kind2, start2, end2 in _boxes(data, start, end):
                if kind2 == b"uuid" and data[start2:start2 + 16] == _CANON_UUID:
                    for kind3, start3, end3 in _boxes(data, start2 + 16, end2):
                        if kind3 == name:
                            return data[start3:end3]
    except (OSError, struct.error):
        pass
    return b""


def _cr3_camera(path: Path) -> tuple[str, str]:
    """Canon CR3 的 Make、Model（在 CMT1）。"""
    tags = _tiff_tags(_cr3_block(path, b"CMT1"), (0x010F, 0x0110))
    return str(tags.get(0x010F, "")), str(tags.get(0x0110, ""))


def _join_camera(make: str, model: str) -> str | None:
    """型號前面補廠牌（Sony 的型號只有 ILCE-7M3）；型號本來就以廠牌開頭的不重複。"""
    brand = make.split(" ")[0].rstrip(",")
    if model and brand and not model.lower().startswith(brand.lower()):
        model = f"{brand} {model}"
    return model or brand or None


def _camera(path: Path) -> str | None:
    """相機型號（EXIF 的 Make + Model，例如 SONY ILCE-7M3）；讀不到回傳 None。
    RAF、CR3 的 EXIF 一般的讀法讀不到，改讀它們自己格式裡的型號（同樣是相機寫的）。"""
    suffix = path.suffix.lower()
    if suffix == ".raf":
        return _raf_camera(path)
    if suffix == ".cr3":
        return _join_camera(*_cr3_camera(path))
    try:
        import exifread

        logging.getLogger("exifread").setLevel(logging.ERROR)  # CR3、RAF 讀不到時不要印警告
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, details=False)
    except Exception:  # noqa: BLE001  讀不到不影響量測
        return None
    return _join_camera(str(tags.get("Image Make", "")).strip(), str(tags.get("Image Model", "")).strip())


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
    camera = _camera(path)
    if camera and camera.isascii() and camera.isprintable():
        header["INSTRUME"] = camera
    return data, header
