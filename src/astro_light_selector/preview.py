"""清單預覽用的縮圖：讀一張 light frame、做成看得出星點的自動拉伸影像。

- 彩色（2×2 Bayer 的 FITS 或相機 RAW）：super-pixel 拆成 R / G / B，各色分開拉伸（背景自動變中性）
- 單色、Fujifilm X-Trans：合成後的灰階
- 拉伸方式類似 PixInsight 的 STF：以中位數和 MAD 決定黑點，再用 midtone transfer 把背景拉到約 25% 亮度
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .metrics import _cfa_bin, _superpixel, load_image

TARGET_BACKGROUND = 0.25
SHADOW_CLIP = 2.8   # 黑點 = 中位數 − 2.8 × MAD


@dataclass
class Preview:
    image: np.ndarray    # 合成後、已拉伸到 0~1 的影像；(H, W) 灰階或 (H, W, 3) 彩色
    scale: int           # 一格代表原始影像幾個像素（super-pixel 的倍數）

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


def make_preview(path: Path) -> Preview:
    data, header = load_image(path)
    n = _cfa_bin(header)
    pattern = str(header.get("BAYERPAT") or "")
    if n == 2 and len(pattern) >= 4:
        img = _bayer_rgb(data, pattern)
    else:
        img = _superpixel(data, n) if n > 1 else data
    step = max(1, max(img.shape[:2]) // 1000)  # 統計用的取樣
    if img.ndim == 3:
        img = np.dstack([stretch(img[..., c], img[::step, ::step, c]) for c in range(3)])
    else:
        img = stretch(img, img[::step, ::step])
    return Preview(image=img, scale=n)


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
