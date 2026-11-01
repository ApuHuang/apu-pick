"""像場分析：把畫面切成 3×3，每區分開量星點的 FWHM、離心率和拉長的方向，看感光元件有沒有傾斜、像場彎不彎。

- 傾斜：四個角的 FWHM 最大減最小，除以四角平均（百分比，跟 ASTAP 的算法一樣）
- 場曲：四角平均比中間大多少（百分比）
- 拉長方向：四個角的星點如果都朝中心（或都繞著中心）拉長，常見是平場鏡／減焦鏡的後焦距不對；
  只有一側拉長比較像傾斜

只看器材，不影響評分。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.stats import sigma_clipped_stats
from photutils.detection import IRAFStarFinder
from scipy.ndimage import uniform_filter

from .metrics import (CUTOUT_HALF, DETECT_FWHM, DETECT_SIGMA, HOT_PIXEL_CONCENTRATION, SIGMA_TO_FWHM, _cfa_bin,
                      _saturation_level, _superpixel, load_image, star_fit)

GRID = 3
STARS_PER_CELL = 25        # 每區擬合幾顆
MAX_ATTEMPTS_PER_CELL = 120
MIN_STARS_PER_CELL = 5     # 少於這麼多顆，這一區不算
CORNERS = {"tl": (0, 0), "tr": (0, GRID - 1), "bl": (GRID - 1, 0), "br": (GRID - 1, GRID - 1)}


@dataclass
class Cell:
    fwhm: float          # 中位數（原始像素）
    eccentricity: float  # 中位數
    angle: float         # 拉長的平均方向（弧度，影像座標）
    alignment: float     # 方向一致的程度 0~1（大家往同一個方向拉長才接近 1）
    n: int               # 擬合成功的星數


@dataclass
class FieldResult:
    cells: list[list[Cell | None]]
    n_frames: int = 1
    notes: list[str] = field(default_factory=list)   # 量不準的原因（i18n 代號）

    def cell(self, name: str) -> Cell | None:
        r, c = CORNERS[name] if name in CORNERS else (GRID // 2, GRID // 2)
        return self.cells[r][c]

    @property
    def center(self) -> Cell | None:
        return self.cells[GRID // 2][GRID // 2]

    @property
    def corners(self) -> dict[str, Cell] | None:
        found = {k: self.cell(k) for k in CORNERS}
        return found if all(found.values()) else None

    @property
    def tilt(self) -> float | None:
        """傾斜（%）：四角 FWHM 最大減最小，除以四角平均。"""
        corners = self.corners
        if corners is None:
            return None
        values = [c.fwhm for c in corners.values()]
        return (max(values) - min(values)) / float(np.mean(values)) * 100

    @property
    def worst_corner(self) -> str | None:
        corners = self.corners
        return max(corners, key=lambda k: corners[k].fwhm) if corners else None

    @property
    def curvature(self) -> float | None:
        """場曲（%）：四角 FWHM 平均比中間大多少。"""
        corners, center = self.corners, self.center
        if corners is None or center is None:
            return None
        return (float(np.mean([c.fwhm for c in corners.values()])) - center.fwhm) / center.fwhm * 100

    def corner_pattern(self) -> str | None:
        """四角的星點怎麼拉長：radial（朝中心）、tangential（繞中心）、one_side（只有一邊）、None（看不出來／不明顯）。"""
        corners, center = self.corners, self.center
        if corners is None or center is None:
            return None
        elongated = {k: c for k, c in corners.items() if c.eccentricity - center.eccentricity >= 0.1
                     and c.alignment >= 0.4}
        if not elongated:
            return None
        sides = ({"tl", "tr"}, {"bl", "br"}, {"tl", "bl"}, {"tr", "br"})
        if any(set(elongated) <= side for side in sides):
            return "one_side"
        kinds = []
        for k, c in elongated.items():
            r, col = CORNERS[k]
            radial = math.atan2(r - GRID // 2, col - GRID // 2)   # 從中心指向這個角
            diff = abs(((c.angle - radial) + math.pi / 2) % math.pi - math.pi / 2)  # 軸向的夾角 0~90°
            kinds.append("radial" if diff < math.radians(30) else "tangential" if diff > math.radians(60) else "")
        for kind in ("radial", "tangential"):
            if kinds.count(kind) >= 3:
                return kind
        return None


def _axial_mean(angles: list[float], weights: list[float]) -> tuple[float, float]:
    """方向的平均（軸向，0 和 180° 一樣）：回傳 (平均方向, 一致程度 0~1)。"""
    z = sum(w * complex(math.cos(2 * a), math.sin(2 * a)) for a, w in zip(angles, weights))
    total = sum(weights) or 1.0
    return math.atan2(z.imag, z.real) / 2, abs(z) / total


def analyze_frame(path: Path) -> FieldResult:
    """量一張的像場。"""
    data, header = load_image(path)
    sat = _saturation_level(header, data)
    scale = _cfa_bin(header)
    if scale > 1:
        data = _superpixel(data, scale)
    _, median, std = sigma_clipped_stats(data, sigma=3.0, maxiters=5)
    notes: list[str] = []
    empty = [[None] * GRID for _ in range(GRID)]
    if not np.isfinite(std) or std <= 0:
        return FieldResult(empty, notes=["field.note.blank"])
    finder = IRAFStarFinder(threshold=DETECT_SIGMA * std, fwhm=DETECT_FWHM, exclude_border=True,
                            peak_max=sat * 0.9, roundness_range=(-5.0, 5.0), sharpness_range=(0.2, 5.0))
    sub = data - median
    sources = finder(sub)
    if sources is None or len(sources) == 0:
        return FieldResult(empty, notes=["field.note.no_stars"])
    box = uniform_filter(sub, size=3) * 9
    xs = np.asarray(sources["x_centroid"])
    ys = np.asarray(sources["y_centroid"])
    xi = np.clip(np.round(xs).astype(int), 0, sub.shape[1] - 1)
    yi = np.clip(np.round(ys).astype(int), 0, sub.shape[0] - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        conc = np.where(box[yi, xi] > 0, sub[yi, xi] / box[yi, xi], np.inf)
    keep = conc <= HOT_PIXEL_CONCENTRATION
    xs, ys, peaks = xs[keep], ys[keep], np.asarray(sources["peak"])[keep]

    h, w = sub.shape
    cells: list[list[Cell | None]] = [[None] * GRID for _ in range(GRID)]
    for r in range(GRID):
        for c in range(GRID):
            inside = np.where((xs >= c * w / GRID) & (xs < (c + 1) * w / GRID)
                              & (ys >= r * h / GRID) & (ys < (r + 1) * h / GRID))[0]
            order = inside[np.argsort(-peaks[inside])]
            fwhms, eccs, angles = [], [], []
            for i in order[:MAX_ATTEMPTS_PER_CELL]:
                if len(fwhms) >= STARS_PER_CELL:
                    break
                x, y = int(round(xs[i])), int(round(ys[i]))
                if not (CUTOUT_HALF <= x < w - CUTOUT_HALF and CUTOUT_HALF <= y < h - CUTOUT_HALF):
                    continue
                fit = star_fit(sub[y - CUTOUT_HALF:y + CUTOUT_HALF + 1, x - CUTOUT_HALF:x + CUTOUT_HALF + 1],
                               DETECT_FWHM / SIGMA_TO_FWHM)
                if fit is not None:
                    fwhms.append(fit[0] * scale)
                    eccs.append(fit[1])
                    angles.append(fit[2])
            if len(fwhms) >= MIN_STARS_PER_CELL:
                angle, alignment = _axial_mean(angles, eccs)
                cells[r][c] = Cell(float(np.median(fwhms)), float(np.median(eccs)), angle, alignment, len(fwhms))
    if any(cell is None for row in cells for cell in row):
        notes.append("field.note.few_stars")
    measured = [cell for row in cells for cell in row if cell is not None]
    if measured and float(np.median([x.fwhm for x in measured])) < 2.0:
        notes.append("field.note.undersampled")
    if measured and float(np.median([x.eccentricity for x in measured])) > 0.6:
        notes.append("field.note.trailed")  # 整張都拖線：FWHM 被拖線拉大，傾斜的數字只能參考
    return FieldResult(cells, notes=notes)


def combine(results: list[FieldResult]) -> FieldResult:
    """幾張的平均（每區取中位數）：單張容易受當下視寧度影響，平均比較穩。"""
    cells: list[list[Cell | None]] = [[None] * GRID for _ in range(GRID)]
    for r in range(GRID):
        for c in range(GRID):
            found = [res.cells[r][c] for res in results if res.cells[r][c] is not None]
            if not found:
                continue
            angle, _ = _axial_mean([x.angle for x in found], [x.eccentricity for x in found])
            cells[r][c] = Cell(float(np.median([x.fwhm for x in found])),
                               float(np.median([x.eccentricity for x in found])), angle,
                               float(np.median([x.alignment for x in found])), sum(x.n for x in found))
    notes = sorted({n for res in results for n in res.notes})
    return FieldResult(cells, n_frames=len(results), notes=notes)
