"""以「最佳範本」為基準的綜合評分挑片。

流程：
1. 每個指標各取整批最好的前 top_frac（預設 10%）平均，組成「範本」。
2. 每張影像每個指標換算成相對範本的比例（範本 = 1.0，上限 1.0），加權後 ×100 得到分數。
3. 用 KDE 找分數分布的峰值（眾數），找不到就退回中位數。
4. keep 門檻 = max(眾數 − margin_k × MAD, 100 × pass_pct)：
   平常由眾數決定；整批偏差、眾數掉到及格線以下時，由及格線頂住。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import gaussian_kde

from .metrics import FrameMetrics


@dataclass
class ScoreConfig:
    top_frac: float = 0.10     # 每個指標取最好的前幾 % 當範本
    pass_pct: float = 0.80     # 及格線：範本分數的百分比
    margin_k: float = 1.5      # 眾數往下容許幾個 MAD
    weights: dict[str, float] = field(default_factory=lambda: {
        "fwhm": 0.35, "eccentricity": 0.30, "n_stars": 0.20, "background": 0.15,
    })


@dataclass
class ScoreResult:
    scores: dict[str, float]          # file -> 分數（0~100）
    components: dict[str, dict[str, float]]  # file -> 各指標的比例
    reference: dict[str, float]       # 範本的各指標值
    mode: float                       # 分數眾數（KDE 峰值）
    mad: float
    threshold: float                  # keep 門檻
    pass_line: float                  # 及格線 = 100 × pass_pct
    limited_by_pass_line: bool        # 門檻是否被及格線頂住


def _top_mean(values: np.ndarray, frac: float, lower_is_better: bool) -> float:
    n = max(1, int(round(len(values) * frac)))
    ordered = np.sort(values)
    top = ordered[:n] if lower_is_better else ordered[-n:]
    return float(top.mean())


def _ratio(metric: str, value: float, ref: float) -> float:
    """換算成 0~1 的相對品質，1 = 跟範本一樣好。"""
    if metric == "fwhm" or metric == "background":
        r = ref / value if value > 0 else 0.0
    elif metric == "eccentricity":
        r = (1.0 - value) / (1.0 - ref) if ref < 1.0 else 0.0
    elif metric == "n_stars":
        r = value / ref if ref > 0 else 0.0
    else:
        raise KeyError(metric)
    return float(np.clip(r, 0.0, 1.0))


def _mode(scores: np.ndarray) -> float:
    if len(scores) < 5 or np.ptp(scores) == 0:
        return float(np.median(scores))
    try:
        kde = gaussian_kde(scores)
        grid = np.linspace(scores.min(), scores.max(), 512)
        return float(grid[np.argmax(kde(grid))])
    except (np.linalg.LinAlgError, ValueError):
        return float(np.median(scores))


def score_frames(frames: list[FrameMetrics], cfg: ScoreConfig) -> ScoreResult:
    ok = [f for f in frames if f.error is None and np.isfinite(f.fwhm) and np.isfinite(f.eccentricity)]
    if not ok:
        raise ValueError("沒有任何一張能量測，無法評分")

    lower_is_better = {"fwhm": True, "eccentricity": True, "n_stars": False, "background": True}
    columns = {m: np.array([getattr(f, m) for f in ok], dtype=float) for m in cfg.weights}
    reference = {m: _top_mean(columns[m], cfg.top_frac, lower_is_better[m]) for m in cfg.weights}

    total_w = sum(cfg.weights.values())
    scores: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}
    for f in ok:
        comp = {m: _ratio(m, getattr(f, m), reference[m]) for m in cfg.weights}
        components[f.file] = comp
        scores[f.file] = 100.0 * sum(cfg.weights[m] * comp[m] for m in cfg.weights) / total_w

    arr = np.array(list(scores.values()))
    mode = _mode(arr)
    mad = float(np.median(np.abs(arr - np.median(arr))) * 1.4826)
    pass_line = 100.0 * cfg.pass_pct
    by_mode = mode - cfg.margin_k * mad
    threshold = max(by_mode, pass_line)

    return ScoreResult(
        scores=scores, components=components, reference=reference,
        mode=mode, mad=mad, threshold=threshold, pass_line=pass_line,
        limited_by_pass_line=pass_line > by_mode,
    )
