"""單張 light frame 的品質指標計算。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from scipy.optimize import least_squares
from photutils.detection import IRAFStarFinder

# 星點偵測門檻（背景雜訊的倍數）
DETECT_SIGMA = 5.0
# 偵測時假設的 FWHM（像素），只影響偵測核，不影響量測結果
DETECT_FWHM = 4.0
# 星點數統計最多取多少顆（避免大圖太慢）
MAX_STARS = 2000
# 拿最亮的幾顆做 2D Gaussian 擬合量 FWHM / 離心率
FIT_STARS = 100
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
    snr: float             # 星點峰值 / 背景雜訊 的中位數
    saturated_frac: float  # 飽和像素比例
    exposure: float | None
    filter: str | None
    date_obs: str | None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def load_image(path: Path) -> tuple[np.ndarray, fits.Header]:
    """讀取 FITS，回傳 2D float32 影像與 header。"""
    with fits.open(path, memmap=False) as hdul:
        hdu = next(h for h in hdul if h.data is not None)
        data = np.asarray(hdu.data, dtype=np.float32)
        header = hdu.header

    # 3D（例如已 debayer 的 RGB）→ 取平均
    if data.ndim == 3:
        data = data.mean(axis=0) if data.shape[0] <= 4 else data.mean(axis=2)
    if data.ndim != 2:
        raise ValueError(f"不支援的影像維度: {data.shape}")

    return data, header


def _is_bayer(header: fits.Header) -> bool:
    return bool(header.get("BAYERPAT")) or "XBAYROFF" in header


def _superpixel(data: np.ndarray) -> np.ndarray:
    """2x2 super-pixel 平均，避免 Bayer 馬賽克干擾星點偵測。"""
    h, w = data.shape
    h -= h % 2
    w -= w % 2
    d = data[:h, :w]
    return (d[0::2, 0::2] + d[0::2, 1::2] + d[1::2, 0::2] + d[1::2, 1::2]) / 4.0


def _saturation_level(header: fits.Header, data: np.ndarray) -> float:
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
    lb = [0.0, h - 3, h - 3, 0.3, 0.3, -np.pi, -np.inf]
    ub = [np.inf, h + 3, h + 3, h, h, np.pi, np.inf]
    try:
        res = least_squares(lambda p: (_gauss2d(p) - cutout).ravel(), p0, bounds=(lb, ub), max_nfev=200)
    except ValueError:
        return None
    if not res.success:
        return None
    _, _, _, sx, sy, _, _ = res.x
    big, small = max(sx, sy), min(sx, sy)
    fwhm = SIGMA_TO_FWHM * float(np.sqrt(sx * sy))
    ecc = float(np.sqrt(1 - (small / big) ** 2))
    return fwhm, ecc


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
        return FrameMetrics(**base, error=f"讀取失敗: {exc}")

    base.update(
        exposure=header.get("EXPTIME") or header.get("EXPOSURE"),
        filter=header.get("FILTER"),
        date_obs=header.get("DATE-OBS"),
    )

    sat_level = _saturation_level(header, data)
    saturated_frac = float((data >= sat_level * 0.98).mean())

    # OSC 影像先做 2x2 super-pixel，量到的 FWHM 要乘回 2 還原成原始像素尺度
    scale = 1.0
    if _is_bayer(header):
        data = _superpixel(data)
        scale = 2.0

    _, bkg_median, bkg_std = sigma_clipped_stats(data, sigma=3.0, maxiters=5)
    base.update(background=float(bkg_median), noise=float(bkg_std), saturated_frac=saturated_frac)
    if not np.isfinite(bkg_std) or bkg_std <= 0:
        return FrameMetrics(**base, error="背景雜訊為 0，可能是空白影像")

    finder = IRAFStarFinder(
        threshold=DETECT_SIGMA * bkg_std,
        fwhm=DETECT_FWHM,
        n_brightest=MAX_STARS,
        exclude_border=True,
        peak_max=sat_level * 0.9,  # 排除飽和星
        # 放寬 roundness / sharpness 過濾：拖線的星、欠取樣的小星都要能偵測到，
        # 這樣才能靠離心率把拖線的整張 reject（IRAFStarFinder 預設 sharpness 下限 0.5 會濾掉小星）
        roundness_range=(-5.0, 5.0),
        sharpness_range=(0.2, 5.0),
    )
    sub = data - bkg_median
    sources = finder(sub)
    if sources is None or len(sources) == 0:
        return FrameMetrics(**base, error="偵測不到星點")

    snr_med = float(np.median(sources["peak"] / bkg_std))

    # IRAFStarFinder 給的 fwhm 對不同 seeing 幾乎沒反應，改對最亮的幾顆做 Gaussian 擬合
    sources.sort("peak", reverse=True)
    fwhms, eccs = [], []
    h, w = sub.shape
    for row in sources[:FIT_STARS]:
        x, y = int(round(row["x_centroid"])), int(round(row["y_centroid"]))
        if CUTOUT_HALF <= x < w - CUTOUT_HALF and CUTOUT_HALF <= y < h - CUTOUT_HALF:
            cut = sub[y - CUTOUT_HALF:y + CUTOUT_HALF + 1, x - CUTOUT_HALF:x + CUTOUT_HALF + 1]
            shape = _star_shape(cut, DETECT_FWHM / SIGMA_TO_FWHM)
            if shape is not None:
                fwhms.append(shape[0])
                eccs.append(shape[1])
    if not fwhms:
        return FrameMetrics(**base, error="星點太靠近邊緣，無法量測形狀")

    base.update(
        n_stars=int(len(sources)),
        fwhm=float(np.median(fwhms)) * scale,
        eccentricity=float(np.median(eccs)),
        snr=snr_med,
    )
    return FrameMetrics(**base)
