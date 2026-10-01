"""單張 light frame 的品質指標計算。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from scipy.ndimage import uniform_filter
from scipy.optimize import least_squares
from photutils.detection import IRAFStarFinder

from .rawfile import RAW_SUFFIXES, load_raw
from .sky import sky_info

FITS_SUFFIXES = {".fit", ".fits", ".fts"}
# 能挑片的所有檔案：FITS 加上各廠牌相機的 RAW
IMAGE_SUFFIXES = FITS_SUFFIXES | RAW_SUFFIXES


def is_image(path: Path) -> bool:
    """能挑片的檔案。macOS 在 exFAT / 網路磁碟上會留下「._檔名.fit」附屬檔，副檔名一樣但不是影像。"""
    return path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith(".")

# 星點偵測門檻（背景雜訊的倍數）
DETECT_SIGMA = 5.0
# 偵測時假設的 FWHM（像素），只影響偵測核，不影響量測結果
DETECT_FWHM = 4.0
# 拿最亮的幾顆做 2D Gaussian 擬合量 FWHM / 離心率
FIT_STARS = 100
# 最多試擬合幾個偵測點才湊滿 FIT_STARS 顆（最亮的不一定是星）
MAX_FIT_ATTEMPTS = 1000
# 擬合成功的星至少要有這麼多顆，否則視為量測失敗（例如整張被雲遮住、只剩熱像素）
MIN_FIT_STARS = 10
# 熱像素：亮度集中在中心那一格的比例超過此值（3×3 內）。星點再小也會分到周圍：
# 單眼短焦的小星約 0.4~0.6，熱像素接近 1。熱像素不算星點數、也不拿來量星形
HOT_PIXEL_CONCENTRATION = 0.7
# 擬合出的 sigma 小於此值（像素）當作擬合失敗；熱像素已經先用集中度排除，這裡只是保險。
# 原本是 0.6，會把單眼配短焦鏡頭、合成 super-pixel 後只有 ~0.5 的真星點誤判掉
MIN_SIGMA = 0.3
# 擬合用的切片半寬（像素）
CUTOUT_HALF = 10
SIGMA_TO_FWHM = 2.3548


@dataclass
class FrameMetrics:
    file: str
    n_stars: int
    fwhm: float            # 星點 FWHM 中位數（像素）
    eccentricity: float    # 星點離心率中位數，0 = 正圓
    background: float      # 背景亮度中位數（ADU）
    noise: float           # 背景雜訊標準差（ADU）
    snr: float             # 量星形用的亮星：峰值 / 背景雜訊 的中位數（薄雲、背景變亮都會讓它下降）
    saturated_frac: float  # 飽和像素比例
    exposure: float | None
    filter: str | None
    date_obs: str | None
    error: str | None = None
    # 拍攝當下的天空（header 有座標、地點時才有）
    altitude: float | None = None      # 目標仰角（度）
    moon_alt: float | None = None      # 月亮仰角（度）
    moon_sep: float | None = None      # 月亮離目標（度）
    moon_illum: float | None = None    # 月相：被照亮的比例 0~1
    # 0.5 新增：依 ISO／增益分組用
    iso: float | None = None           # 相機 RAW 的 ISO
    gain: float | None = None          # 天文相機的 GAIN

    def to_dict(self) -> dict:
        return asdict(self)


def load_image(path: Path) -> tuple[np.ndarray, fits.Header]:
    """讀取 FITS 或相機 RAW，回傳 2D float32 影像與 header（RAW 的欄位也轉成 FITS 的名稱）。"""
    if path.suffix.lower() in RAW_SUFFIXES:
        return load_raw(path)
    with fits.open(path, memmap=False) as hdul:
        hdu = next(h for h in hdul if h.data is not None)
        data = np.asarray(hdu.data, dtype=np.float32)
        header = hdu.header

    # 3D（例如已 debayer 的 RGB）→ 取平均
    if data.ndim == 3:
        data = data.mean(axis=0) if data.shape[0] <= 4 else data.mean(axis=2)
    if data.ndim != 2:
        raise ValueError(f"unsupported image shape {data.shape}")

    return data, header


def _cfa_bin(header: fits.Header) -> int:
    """彩色感光元件要先合成幾格：一般 Bayer 2×2、Fujifilm X-Trans 3×3；單色 1（不合成）。"""
    if header.get("CFAGRID"):
        return int(header["CFAGRID"])
    return 2 if header.get("BAYERPAT") or "XBAYROFF" in header else 1


def _superpixel(data: np.ndarray, n: int = 2) -> np.ndarray:
    """n×n 平均，避免彩色馬賽克干擾星點偵測（n=2 就是 Bayer 的 super-pixel）。"""
    h, w = data.shape
    h -= h % n
    w -= w % n
    return data[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))


def _saturation_level(header: fits.Header, data: np.ndarray) -> float:
    if header.get("SATLEVEL"):  # 相機 RAW：白位扣掉黑位
        return float(header["SATLEVEL"])
    bitpix = header.get("BITPIX", 16)
    if bitpix in (-32, -64):
        return float(data.max())
    depth = header.get("BITDEPTH")
    if depth:
        return float(2 ** int(depth) - 1)
    # 16-bit 整數但相機常是 12/14-bit：以實際最大值為準
    return float(max(data.max(), 1.0))


_YY, _XX = np.indices((2 * CUTOUT_HALF + 1, 2 * CUTOUT_HALF + 1))


def _gauss2d(p: np.ndarray) -> np.ndarray:
    amp, x0, y0, sx, sy, theta, offset = p
    c, s = np.cos(theta), np.sin(theta)
    xr = (_XX - x0) * c + (_YY - y0) * s
    yr = -(_XX - x0) * s + (_YY - y0) * c
    return amp * np.exp(-xr**2 / (2 * sx**2) - yr**2 / (2 * sy**2)) + offset


def _star_shape(cutout: np.ndarray, sigma0: float) -> tuple[float, float] | None:
    """對星點切片做橢圓 2D Gaussian 擬合，回傳 (FWHM 像素, 離心率)。

    FWHM 取長短軸 sigma 的幾何平均換算；離心率 0 = 正圓。
    """
    h = CUTOUT_HALF
    p0 = [float(cutout.max()), h, h, sigma0, sigma0, 0.0, 0.0]
    lb = [0.0, h - 3, h - 3, MIN_SIGMA / 2, MIN_SIGMA / 2, -np.pi, -np.inf]
    ub = [np.inf, h + 3, h + 3, h, h, np.pi, np.inf]
    try:
        res = least_squares(lambda p: (_gauss2d(p) - cutout).ravel(), p0, bounds=(lb, ub), max_nfev=200)
    except ValueError:
        return None
    if not res.success:
        return None
    _, _, _, sx, sy, _, _ = res.x
    big, small = max(sx, sy), min(sx, sy)
    if small < MIN_SIGMA:
        return None
    fwhm = SIGMA_TO_FWHM * float(np.sqrt(sx * sy))
    ecc = float(np.sqrt(1 - (small / big) ** 2))
    return fwhm, ecc


def _number(header: fits.Header, *keys: str) -> float | None:
    for key in keys:
        try:
            v = float(header[key])
        except (KeyError, TypeError, ValueError):
            continue
        if v == v:
            return v
    return None


def measure(path: Path) -> FrameMetrics:
    """計算單張 light frame 的品質指標。失敗時回傳帶 error 的結果。"""
    base = dict(
        file=str(path), n_stars=0, fwhm=np.nan, eccentricity=np.nan,
        background=np.nan, noise=np.nan, snr=np.nan, saturated_frac=np.nan,
        exposure=None, filter=None, date_obs=None,
    )
    try:
        data, header = load_image(path)
    except Exception as exc:  # noqa: BLE001
        return FrameMetrics(**base, error=f"read_failed:{exc}")

    base.update(
        exposure=header.get("EXPTIME") or header.get("EXPOSURE"),
        filter=header.get("FILTER"),
        date_obs=header.get("DATE-OBS"),
        iso=_number(header, "ISO", "ISOSPEED"),
        gain=_number(header, "GAIN"),
    )
    sky = sky_info(header)
    if sky is not None:
        base.update(altitude=sky.altitude, moon_alt=sky.moon_alt, moon_sep=sky.moon_sep, moon_illum=sky.moon_illum)

    sat_level = _saturation_level(header, data)
    saturated_frac = float((data >= sat_level * 0.98).mean())

    # 彩色影像先合成 super-pixel，量到的 FWHM 要乘回去還原成原始像素尺度
    scale = _cfa_bin(header)
    if scale > 1:
        data = _superpixel(data, scale)

    _, bkg_median, bkg_std = sigma_clipped_stats(data, sigma=3.0, maxiters=5)
    base.update(background=float(bkg_median), noise=float(bkg_std), saturated_frac=saturated_frac)
    if not np.isfinite(bkg_std) or bkg_std <= 0:
        return FrameMetrics(**base, error="blank")

    finder = IRAFStarFinder(
        threshold=DETECT_SIGMA * bkg_std,
        fwhm=DETECT_FWHM,
        exclude_border=True,
        peak_max=sat_level * 0.9,  # 排除飽和星
        # 放寬 roundness / sharpness 過濾：拖線的星、欠取樣的小星都要能偵測到，
        # 這樣才能靠離心率把拖線的整張 reject（IRAFStarFinder 預設 sharpness 下限 0.5 會濾掉小星）
        roundness_range=(-5.0, 5.0),
        sharpness_range=(0.2, 5.0),
    )
    sub = data - bkg_median
    sources = finder(sub)
    if sources is not None and len(sources) > 0:
        # 熱像素（長曝、沒扣暗場的單眼特別多）亮度全在一格裡，先排除
        box = uniform_filter(sub, size=3) * 9
        xi = np.clip(np.round(np.asarray(sources["x_centroid"])).astype(int), 0, sub.shape[1] - 1)
        yi = np.clip(np.round(np.asarray(sources["y_centroid"])).astype(int), 0, sub.shape[0] - 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            conc = np.where(box[yi, xi] > 0, sub[yi, xi] / box[yi, xi], np.inf)
        sources = sources[conc <= HOT_PIXEL_CONCENTRATION]
    if sources is None or len(sources) == 0:
        return FrameMetrics(**base, error="no_stars")

    base.update(n_stars=int(len(sources)))

    # IRAFStarFinder 給的 fwhm 對不同 seeing 幾乎沒反應，改對最亮的幾顆做 Gaussian 擬合；
    # 從亮到暗一路往下，湊滿 FIT_STARS 顆擬合成功的星為止
    sources.sort("peak", reverse=True)
    fwhms, eccs, peaks = [], [], []
    h, w = sub.shape
    for row in sources[:MAX_FIT_ATTEMPTS]:
        if len(fwhms) >= FIT_STARS:
            break
        x, y = int(round(row["x_centroid"])), int(round(row["y_centroid"]))
        if CUTOUT_HALF <= x < w - CUTOUT_HALF and CUTOUT_HALF <= y < h - CUTOUT_HALF:
            cut = sub[y - CUTOUT_HALF:y + CUTOUT_HALF + 1, x - CUTOUT_HALF:x + CUTOUT_HALF + 1]
            shape = _star_shape(cut, DETECT_FWHM / SIGMA_TO_FWHM)
            if shape is not None:
                fwhms.append(shape[0])
                eccs.append(shape[1])
                peaks.append(float(row["peak"]))
    if len(fwhms) < MIN_FIT_STARS:
        return FrameMetrics(**base, error=f"few_fit_stars:{len(fwhms)}")

    base.update(
        fwhm=float(np.median(fwhms)) * scale,
        eccentricity=float(np.median(eccs)),
        snr=float(np.median(peaks)) / float(bkg_std),
    )
    return FrameMetrics(**base)
