"""清單預覽用的縮圖：讀一張 light frame、做成看得出星點的自動拉伸影像。

- 彩色（2×2 Bayer 的 FITS 或相機 RAW）：super-pixel 拆成 R / G / B，各色分開拉伸（背景自動變中性）
- 單色、Fujifilm X-Trans：合成後的灰階
- 拉伸方式類似 PixInsight 的 STF：以中位數和 MAD 決定黑點，再用 midtone transfer 把背景拉到約 25% 亮度
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .metrics import _cfa_bin, _superpixel, load_image

TARGET_BACKGROUND = 0.25
SHADOW_CLIP = 2.8   # 黑點 = 中位數 − 2.8 × MAD


HIST_BINS = 160
# 檔頭裡值得列在「資訊」的欄位（拍攝軟體常寫的）；相機 RAW 只有曝光、ISO、時間、相機
INFO_KEYS = ("DATE-OBS", "EXPTIME", "ISO", "GAIN", "OFFSET", "FILTER", "INSTRUME", "TELESCOP", "FOCALLEN",
             "XPIXSZ", "XBINNING", "YBINNING", "CCD-TEMP", "SET-TEMP", "OBJECT", "BAYERPAT")


@dataclass
class Preview:
    image: np.ndarray    # 合成後、已拉伸到 0~1 的影像；(H, W) 灰階或 (H, W, 3) 彩色
    scale: int           # 一格代表原始影像幾個像素（super-pixel 的倍數）
    # 亮度直方圖，每個通道一列（彩色是 R、G、B），另外一份合成亮度（_lum）：
    # 拉伸前是線性的 ADU（對數間隔的 hist_edges）；拉伸後跟預覽同一套拉伸，0~1 分 256 格
    hist: np.ndarray | None = None
    hist_lum: np.ndarray | None = None
    hist_edges: np.ndarray | None = None
    stretched_hist: np.ndarray | None = None
    stretched_lum: np.ndarray | None = None
    saturation: float | None = None                          # 已知的飽和值（不知道是 None）
    background: list[float] = field(default_factory=list)   # 各通道背景（中位數，ADU）
    stats: list[dict] = field(default_factory=list)          # 各通道 median / low / high（ADU）/ saturated（比例）
    info: dict = field(default_factory=dict)                 # 檔頭欄位（INFO_KEYS）＋ 影像尺寸 SIZE

    @property
    def size(self) -> tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]


def _bayer_rgb(data: np.ndarray, pattern: str) -> np.ndarray:
    """2×2 Bayer 拆成 super-pixel 的 RGB（兩格綠色取平均）。"""
    h, w = data.shape
    d = data[:h - h % 2, :w - w % 2]
    cells = {"R": [], "G": [], "B": []}
    for i, color in enumerate(pattern[:4].upper()):
        cells.setdefault(color, []).append(d[i // 2::2, i % 2::2])
    if not cells["R"] or not cells["G"] or not cells["B"]:
        return _superpixel(data, 2)
    return np.dstack([np.mean(cells[c], axis=0) for c in "RGB"])


def _mtf(m: float, x: np.ndarray) -> np.ndarray:
    """PixInsight 的 midtone transfer function。"""
    return ((m - 1) * x) / ((2 * m - 1) * x - m)


def stretch(channel: np.ndarray, sample: np.ndarray | None = None) -> np.ndarray:
    """自動拉伸一個通道到 0~1。sample 是拿來算統計的（通常是縮小過的同一張），省時間。"""
    s = channel if sample is None else sample
    s = s[np.isfinite(s)]
    med = float(np.median(s))
    mad = float(np.median(np.abs(s - med))) * 1.4826
    top = float(np.percentile(s, 99.99))
    low = med - SHADOW_CLIP * mad
    span = max(top - low, 1e-6)
    x = np.clip((channel - low) / span, 0, 1)
    med_n = float(np.clip((med - low) / span, 1e-6, 1 - 1e-6))
    t = TARGET_BACKGROUND
    m = med_n * (1 - t) / (med_n - 2 * t * med_n + t)
    return np.clip(_mtf(m, x), 0, 1).astype(np.float32)


def _known_saturation(header, data: np.ndarray) -> float | None:
    """真的會爆掉的值：RAW 的白位、BITDEPTH，或 16-bit 整數的 65535；32-bit 浮點不知道就回傳 None。"""
    if header.get("SATLEVEL"):
        return float(header["SATLEVEL"])
    if header.get("BITDEPTH"):
        return float(2 ** int(header["BITDEPTH"]) - 1)
    if header.get("BITPIX", 16) in (8, 16):
        return 255.0 if header.get("BITPIX") == 8 else 65535.0
    return None


def _samples(img: np.ndarray) -> list[np.ndarray]:
    """每個通道取樣約一百萬點（直方圖、統計用，夠了），彩色的最後再加一份合成亮度。"""
    channels = [img[..., c] for c in range(3)] if img.ndim == 3 else [img]
    step = max(1, int(np.sqrt(channels[0].size / 1_000_000)))
    samples = [np.nan_to_num(c[::step, ::step].ravel()) for c in channels]
    return samples + ([np.mean(samples, axis=0)] if len(samples) == 3 else [samples[0]])


def _histogram(img: np.ndarray, saturation: float | None):
    """拉伸前的亮度直方圖（x 軸對數間隔，背景的山峰和亮星都看得到）與各通道的統計。"""
    *channels, lum = _samples(img)
    top = saturation or max(float(s.max()) for s in channels)
    edges = np.geomspace(1.0, max(top * 1.02, 2.0), HIST_BINS + 1)

    def count(s: np.ndarray) -> np.ndarray:
        return np.histogram(np.clip(s, 1.0, edges[-1]), bins=edges)[0]

    stats = [{"median": float(np.median(s)), "low": float(np.percentile(s, 0.1)), "high": float(s.max()),
              "saturated": float((s >= saturation * 0.98).mean()) if saturation else None} for s in channels]
    return np.array([count(s) for s in channels]), count(lum), edges, [st["median"] for st in stats], stats


def _stretched_histogram(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """拉伸後（0~1）的直方圖，256 格；跟預覽看到的亮度一樣，像一般修圖軟體的直方圖。

    相機的 ADU 是一階一階的整數，拉伸後會落在分開的幾格，畫出來像梳子；稍微抹平（高斯，約 2 格）再畫。
    """
    *channels, lum = _samples(img)
    x = np.arange(-6, 7)
    kernel = np.exp(-x ** 2 / (2 * 2.0 ** 2))
    kernel /= kernel.sum()

    def count(s: np.ndarray) -> np.ndarray:
        return np.convolve(np.histogram(s, bins=256, range=(0.0, 1.0))[0], kernel, mode="same")

    return np.array([count(s) for s in channels]), count(lum)


def make_preview(path: Path) -> Preview:
    data, header = load_image(path)
    n = _cfa_bin(header)
    pattern = str(header.get("BAYERPAT") or "")
    if n == 2 and len(pattern) >= 4:
        img = _bayer_rgb(data, pattern)
    else:
        img = _superpixel(data, n) if n > 1 else data
    saturation = _known_saturation(header, data)
    hist, hist_lum, edges, background, stats = _histogram(img, saturation)
    info = {k: header[k] for k in INFO_KEYS if header.get(k) not in (None, "")}
    info["SIZE"] = (data.shape[1], data.shape[0])
    step = max(1, max(img.shape[:2]) // 1000)  # 統計用的取樣
    if img.ndim == 3:
        img = np.dstack([stretch(img[..., c], img[::step, ::step, c]) for c in range(3)])
    else:
        img = stretch(img, img[::step, ::step])
    stretched, stretched_lum = _stretched_histogram(img)
    return Preview(image=img, scale=n, hist=hist, hist_lum=hist_lum, hist_edges=edges, stretched_hist=stretched,
                   stretched_lum=stretched_lum, saturation=saturation, background=background, stats=stats, info=info)


def to_pil(image: np.ndarray) -> Image.Image:
    arr = (np.clip(image, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB" if arr.ndim == 3 else "L")


def thumbnail(preview: Preview, max_width: int, max_height: int) -> Image.Image:
    """整張縮到指定大小以內。"""
    img = to_pil(preview.image)
    img.thumbnail((max_width, max_height), Image.LANCZOS)
    return img


def closeup(preview: Preview, cx: float, cy: float, size: int, zoom: int = 2) -> Image.Image:
    """以 (cx, cy)（0~1 的相對位置）為中心裁一塊 size×size，再放大 zoom 倍（最近鄰，看得出星形）。

    用灰階：單眼的色彩雜訊會干擾判斷，看星形只需要亮度。
    """
    h, w = preview.image.shape[:2]
    half = size // 2
    x = int(np.clip(cx * w, half, max(half, w - half)))
    y = int(np.clip(cy * h, half, max(half, h - half)))
    crop = preview.image[max(0, y - half):y + half, max(0, x - half):x + half]
    if crop.ndim == 3:
        crop = crop.mean(axis=2)
    img = to_pil(crop)
    return img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)
