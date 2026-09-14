"""依品質指標決定 keep / reject。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .metrics import FrameMetrics
from .scoring import ScoreConfig, ScoreResult, score_frames


@dataclass
class Thresholds:
    """挑片門檻。

    相對門檻（*_k）以整批影像的中位數 ± k × MAD 為準，可適應不同夜晚的條件；
    絕對門檻（max_* / min_*）為硬性限制，設為 None 表示不使用。
    """
    # 相對門檻（MAD 倍數）
    # 預設值是用 NGC7635 實拍 296 張對照肉眼挑片調出來的：星形（FWHM / 離心率）抓嚴，
    # 背景放寬——傍晚剛開拍背景偏亮但星點正常的張數，肉眼通常不會丟
    fwhm_k: float = 1.5        # FWHM 高於 中位數 + k*MAD → reject
    stars_k: float = 2.0       # 星點數低於 中位數 - k*MAD → reject
    ecc_k: float = 1.5         # 離心率高於 中位數 + k*MAD → reject
    background_k: float = 3.0  # 背景高於 中位數 + k*MAD → reject（雲、光害、月光）
    # 絕對門檻
    max_fwhm: float | None = None
    min_stars: int | None = 20
    max_eccentricity: float | None = 0.7
    max_saturated_frac: float | None = None
    # 是否把讀取/偵測失敗的影像視為 reject
    reject_errors: bool = True


@dataclass
class Decision:
    metrics: FrameMetrics
    keep: bool
    reasons: list[str] = field(default_factory=list)
    score: float | None = None


def _mad(values: np.ndarray) -> float:
    med = np.median(values)
    return float(np.median(np.abs(values - med)) * 1.4826)  # 換算成等效標準差


def _finite(values: list[float | None]) -> np.ndarray:
    return np.array([v for v in values if v is not None and np.isfinite(v)], dtype=float)


def _spread(arr: np.ndarray, rel_floor: float, abs_floor: float) -> float:
    """MAD 加上下限，避免整批影像幾乎一樣時門檻縮到 0，把正常的雜訊起伏也 reject。"""
    med = float(np.median(arr))
    return max(_mad(arr), rel_floor * abs(med), abs_floor)


def _upper_limit(values: list[float | None], k: float,
                 rel_floor: float = 0.05, abs_floor: float = 0.0) -> float | None:
    arr = _finite(values)
    if len(arr) < 3:
        return None
    return float(np.median(arr) + k * _spread(arr, rel_floor, abs_floor))


def _lower_limit(values: list[float | None], k: float,
                 rel_floor: float = 0.05, abs_floor: float = 0.0) -> float | None:
    arr = _finite(values)
    if len(arr) < 3:
        return None
    return float(np.median(arr) - k * _spread(arr, rel_floor, abs_floor))


def select(frames: list[FrameMetrics], th: Thresholds) -> list[Decision]:
    """對整批影像做挑片。相對門檻只用成功量測的影像來計算統計。"""
    ok = [f for f in frames if f.error is None]

    fwhm_hi = _upper_limit([f.fwhm for f in ok], th.fwhm_k)
    stars_lo = _lower_limit([f.n_stars for f in ok], th.stars_k)
    ecc_hi = _upper_limit([f.eccentricity for f in ok], th.ecc_k, abs_floor=0.03)
    bkg_hi = _upper_limit([f.background for f in ok], th.background_k)

    decisions: list[Decision] = []
    for f in frames:
        reasons: list[str] = []
        if f.error is not None:
            if th.reject_errors:
                reasons.append(f"error: {f.error}")
            decisions.append(Decision(f, keep=not reasons, reasons=reasons))
            continue

        has_ecc = np.isfinite(f.eccentricity)

        # 相對門檻
        if fwhm_hi is not None and f.fwhm > fwhm_hi:
            reasons.append(f"FWHM {f.fwhm:.2f} > {fwhm_hi:.2f}")
        if stars_lo is not None and f.n_stars < stars_lo:
            reasons.append(f"stars {f.n_stars} < {stars_lo:.0f}")
        if ecc_hi is not None and has_ecc and f.eccentricity > ecc_hi:
            reasons.append(f"ecc {f.eccentricity:.2f} > {ecc_hi:.2f}")
        if bkg_hi is not None and f.background > bkg_hi:
            reasons.append(f"background {f.background:.0f} > {bkg_hi:.0f}")

        # 絕對門檻
        if th.max_fwhm is not None and f.fwhm > th.max_fwhm:
            reasons.append(f"FWHM {f.fwhm:.2f} > max {th.max_fwhm}")
        if th.min_stars is not None and f.n_stars < th.min_stars:
            reasons.append(f"stars {f.n_stars} < min {th.min_stars}")
        if th.max_eccentricity is not None and has_ecc and f.eccentricity > th.max_eccentricity:
            reasons.append(f"ecc {f.eccentricity:.2f} > max {th.max_eccentricity}")
        if th.max_saturated_frac is not None and f.saturated_frac > th.max_saturated_frac:
            reasons.append(f"saturated {f.saturated_frac:.4f} > max {th.max_saturated_frac}")

        decisions.append(Decision(f, keep=not reasons, reasons=reasons))
    return decisions


def select_by_score(frames: list[FrameMetrics], cfg: ScoreConfig) -> tuple[list[Decision], ScoreResult]:
    """綜合評分挑片：分數低於門檻 → reject；量不出來的一樣 reject。"""
    result = score_frames(frames, cfg)
    decisions: list[Decision] = []
    for f in frames:
        if f.file not in result.scores:
            reason = f"error: {f.error}" if f.error else "無法評分"
            decisions.append(Decision(f, keep=False, reasons=[reason]))
            continue
        sc = result.scores[f.file]
        if sc < result.threshold:
            comp = result.components[f.file]
            worst = min(comp, key=comp.get)
            reasons = [f"score {sc:.1f} < {result.threshold:.1f} (弱項 {worst} {comp[worst]:.2f})"]
        else:
            reasons = []
        decisions.append(Decision(f, keep=not reasons, reasons=reasons, score=sc))
    return decisions, result
