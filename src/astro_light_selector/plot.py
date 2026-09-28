"""畫出各指標隨拍攝順序的變化，keep / reject 分色，方便肉眼確認門檻合不合理。

需要 matplotlib（選用相依：pip install matplotlib）。不經過 pyplot，
同一套畫法可以存成 PNG，也可以畫在視窗介面裡。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.figure import Figure

from .selector import Decision

PANELS = [
    ("score", "分數"),
    ("fwhm", "FWHM (px)"),
    ("eccentricity", "離心率"),
    ("n_stars", "星點數"),
    ("background", "背景 (ADU)"),
]
KEEP_COLOR = "#2a7fd4"
REJECT_COLOR = "#e0533d"

# 中文字型：Windows 有微軟正黑體，其他平台找不到就用預設
matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Noto Sans CJK TC", "PingFang TC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


def _value(d: Decision, key: str) -> float:
    v = d.score if key == "score" else getattr(d.metrics, key)
    return float("nan") if v is None else float(v)


def _robust_limits(y: np.ndarray) -> tuple[float, float] | None:
    """用 2%~98% 分位數定 y 軸範圍，讓天亮、雲那幾張極端值不會把整格壓扁。"""
    y = y[np.isfinite(y)]
    if len(y) < 5:
        return None
    lo, hi = np.percentile(y, [2, 98])
    pad = max(hi - lo, abs(hi) * 0.02, 1e-9) * 0.3
    lo, hi = lo - pad, hi + pad
    if y.min() >= lo and y.max() <= hi:
        return None
    return lo, hi


def draw_decisions(fig: Figure, decisions: list[Decision], thresholds: dict[str, float] | None = None) -> None:
    """清掉 fig 重畫：依分組、DATE-OBS 排序的多格圖。

    thresholds: {分組標籤: keep 門檻}，評分模式時在分數格畫出門檻線。
    """
    fig.clear()
    if not decisions:
        return
    # 先依分組、再依時間，同組連在一起（LRGB 輪流拍時才不會切得零零碎碎）
    ordered = sorted(decisions, key=lambda d: (d.group, d.metrics.date_obs or "", d.metrics.file))
    x = np.arange(len(ordered))
    keep = np.array([d.keep for d in ordered])
    colors = np.where(keep, KEEP_COLOR, REJECT_COLOR)
    panels = [p for p in PANELS if p[0] != "score" or any(d.score is not None for d in ordered)]

    axes = np.atleast_1d(fig.subplots(len(panels), 1, sharex=True))
    for ax, (key, label) in zip(axes, panels):
        y = np.array([_value(d, key) for d in ordered])
        lim = _robust_limits(y)
        if lim is not None:
            lo, hi = lim
            # 超出範圍的點貼在邊上，用三角形標示
            over, under = y > hi, y < lo
            ax.scatter(x[over], np.full(over.sum(), hi), s=18, c=colors[over], marker="^", clip_on=False)
            ax.scatter(x[under], np.full(under.sum(), lo), s=18, c=colors[under], marker="v", clip_on=False)
            y = np.where(over | under, np.nan, y)
            ax.set_ylim(lo, hi)
        ax.scatter(x[keep], y[keep], s=10, c=KEEP_COLOR, label="keep")
        ax.scatter(x[~keep], y[~keep], s=14, c=REJECT_COLOR, marker="x", label="reject")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
        if key == "score" and thresholds:
            for group, th in thresholds.items():
                idx = [i for i, d in enumerate(ordered) if d.group == group]
                if idx:
                    ax.hlines(th, min(idx) - 0.5, max(idx) + 0.5, colors=REJECT_COLOR, linestyles="--", lw=1)

    # 換組的地方畫分隔線，並在最上面一格標出組名
    for i in range(len(ordered)):
        if i > 0 and ordered[i].group != ordered[i - 1].group:
            for ax in axes:
                ax.axvline(i - 0.5, color="gray", lw=0.8, alpha=0.6)
        if ordered[i].group and (i == 0 or ordered[i].group != ordered[i - 1].group):
            axes[0].annotate(ordered[i].group, (i, 1), xycoords=("data", "axes fraction"),
                             xytext=(3, -3), textcoords="offset points", va="top", fontsize=7, color="gray")

    n_keep = int(keep.sum())
    axes[0].set_title(f"共 {len(ordered)} 張，keep {n_keep}，reject {len(ordered) - n_keep}")
    axes[0].legend(loc="lower left", fontsize=8)
    axes[-1].set_xlabel("拍攝順序")


def plot_decisions(decisions: list[Decision], path: Path, thresholds: dict[str, float] | None = None) -> None:
    """畫好存成 PNG。"""
    fig = Figure(figsize=(12, 2.2 * len(PANELS)), layout="constrained")
    draw_decisions(fig, decisions, thresholds)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
