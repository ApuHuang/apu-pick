"""產生合成星場 FITS，供測試與試跑用。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits


def make_star_field(
    shape: tuple[int, int] = (512, 512),
    n_stars: int = 150,
    fwhm: float = 3.0,
    elongation: float = 1.0,   # >1 表示星點被拉長（追蹤不良）
    background: float = 800.0,
    noise: float = 15.0,
    seed: int = 0,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = shape
    img = np.full(shape, background, dtype=np.float64)
    sigma = fwhm / 2.3548
    sx, sy = sigma * elongation, sigma

    ys, xs = np.mgrid[0:h, 0:w]
    xs_c = rng.uniform(10, w - 10, n_stars)
    ys_c = rng.uniform(10, h - 10, n_stars)
    amps = rng.uniform(300, 8000, n_stars)
    for x0, y0, a in zip(xs_c, ys_c, amps):
        x_lo, x_hi = int(max(x0 - 15, 0)), int(min(x0 + 16, w))
        y_lo, y_hi = int(max(y0 - 15, 0)), int(min(y0 + 16, h))
        xx = xs[y_lo:y_hi, x_lo:x_hi]
        yy = ys[y_lo:y_hi, x_lo:x_hi]
        img[y_lo:y_hi, x_lo:x_hi] += a * np.exp(
            -((xx - x0) ** 2) / (2 * sx**2) - ((yy - y0) ** 2) / (2 * sy**2)
        )

    img += rng.normal(0, noise, shape)
    return np.clip(img, 0, 65535).astype(np.uint16)


def write_fits(path: Path, data: np.ndarray, **header) -> Path:
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = header.get("exptime", 120.0)
    hdu.header["FILTER"] = header.get("filter", "L")
    hdu.header["DATE-OBS"] = header.get("date_obs", "2026-09-14T12:00:00")
    for k, v in header.items():
        if k.upper() not in hdu.header:
            hdu.header[k.upper()[:8]] = v
    hdu.writeto(path, overwrite=True)
    return path


def make_session(folder: Path, n_good: int = 8) -> dict[str, list[Path]]:
    """建立一批影像：n_good 張正常 + 幾張各有不同問題的。"""
    folder.mkdir(parents=True, exist_ok=True)
    good, bad = [], []
    for i in range(n_good):
        good.append(write_fits(folder / f"light_{i:03d}.fits", make_star_field(seed=i)))
    bad.append(write_fits(folder / "bad_seeing.fits", make_star_field(fwhm=7.0, seed=100)))
    bad.append(write_fits(folder / "bad_trailing.fits", make_star_field(elongation=2.5, seed=101)))
    bad.append(write_fits(folder / "bad_cloud.fits", make_star_field(n_stars=15, background=3000, seed=102)))
    return {"good": good, "bad": bad}


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples")
    r = make_session(out)
    print(f"已產生 {len(r['good'])} 張正常 + {len(r['bad'])} 張問題影像於 {out}")
