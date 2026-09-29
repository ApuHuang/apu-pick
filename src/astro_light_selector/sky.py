"""從 FITS header 算拍攝當下的天空資訊：目標仰角、月亮仰角、月亮離目標多遠、月相。

需要 header 有目標座標（RA/DEC、OBJCTRA/OBJCTDEC 或 WCS 的 CRVAL1/2）、觀測地點（SITELAT/SITELONG）
和 UTC 的 DATE-OBS；缺任何一項就不算（相機 RAW 通常沒有座標與地點）。
時間用曝光的中間點。用 astropy 內建的星曆，不連網。
"""

from __future__ import annotations

from dataclasses import dataclass

from astropy.io import fits


@dataclass
class SkyInfo:
    altitude: float      # 目標仰角（度）
    moon_alt: float      # 月亮仰角（度，負的表示在地平線下）
    moon_sep: float      # 月亮離目標的角距離（度）
    moon_illum: float    # 月亮被照亮的比例 0~1


def _angle(value: object, hours: bool = False) -> float | None:
    """header 裡的角度：數字（度）或 "23 20 44.5" / "+61:12:10" 這類六十進位字串。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    from astropy.coordinates import Angle
    import astropy.units as u

    try:
        text = str(value).strip()
        if any(c in text for c in " :hdm"):
            return float(Angle(text, unit=u.hourangle if hours else u.deg).deg)
        return float(text)
    except (ValueError, TypeError):
        return None


def sky_info(header: fits.Header) -> SkyInfo | None:
    ra = _angle(header.get("RA"))
    dec = _angle(header.get("DEC"))
    if ra is None or dec is None:
        ra = _angle(header.get("OBJCTRA"), hours=True)
        dec = _angle(header.get("OBJCTDEC"))
    if ra is None or dec is None:
        ra, dec = _angle(header.get("CRVAL1")), _angle(header.get("CRVAL2"))
    lat = _angle(header.get("SITELAT"))
    lon = _angle(header.get("SITELONG"))
    date = header.get("DATE-OBS")
    if None in (ra, dec, lat, lon) or not date:
        return None

    import astropy.units as u
    import numpy as np
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
    from astropy.time import Time
    from astropy.utils import iers

    iers.conf.auto_download = False  # 不連網：精度對仰角、月亮已經足夠
    try:
        when = Time(str(date), scale="utc") + (float(header.get("EXPTIME") or 0) / 2) * u.s
        site = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        frame = AltAz(obstime=when, location=site)
        target = SkyCoord(ra * u.deg, dec * u.deg)
        moon = get_body("moon", when, site)
        sun = get_body("sun", when, site)
        elongation = moon.separation(sun).rad
        return SkyInfo(
            altitude=float(target.transform_to(frame).alt.deg),
            moon_alt=float(moon.transform_to(frame).alt.deg),
            moon_sep=float(moon.separation(target, origin_mismatch="ignore").deg),
            moon_illum=float((1 - np.cos(elongation)) / 2),
        )
    except Exception:  # noqa: BLE001  算不出來就不顯示，不影響挑片
        return None
