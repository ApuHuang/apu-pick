"""畫出各指標隨拍攝順序的變化，保留 / 淘汰分色，方便肉眼確認門檻合不合理。

需要 matplotlib（選用相依：pip install matplotlib）。不經過 pyplot，
同一套畫法可以存成 PNG（淺色），也可以畫在視窗介面裡（深色，與 APU Astro 的暗房主題一致）。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.figure import Figure

from .i18n import tr
from .selector import Decision

# 各格要畫的指標，標題用 tr(f"plot.{key}")
PANELS = ["score", "fwhm", "eccentricity", "n_stars", "background"]

LIGHT = {
    "bg": "white", "axes": "white", "text": "#1a1a1a", "muted": "gray", "spine": "#555555",
    "grid": "#d9d9d9", "keep": "#2a7fd4", "reject": "#e0533d", "threshold": "#e0533d",
    "legend": "white",
}
# 暗房配色：跟 APU Astro 的 DarkroomTheme 同一套色票
DARK = {
    "bg": "#000000", "axes": "#0e0e0e", "text": "#ebebeb", "muted": "#949494", "spine": "#454545",
    "grid": "#2f2f2f", "keep": "#5cadff", "reject": "#ff6b6b", "threshold": "#ffa842",
    "legend": "#1c1c1c",
}

# 中文字型：Windows 有微軟正黑體，Mac 有蘋方，其他平台找不到就用預設
matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", "DejaVu Sans"]
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


def draw_decisions(fig: Figure, decisions: list[Decision], thresholds: dict[str, float] | None = None,
                   dark: bool = False) -> None:
    """清掉 fig 重畫：依分組、DATE-OBS 排序的多格圖。

    thresholds: {分組標籤: 保留門檻}，評分模式時在分數格畫出門檻線。
    """
    theme = DARK if dark else LIGHT
    fig.clear()
    fig.set_facecolor(theme["bg"])
    if not decisions:
        return
    # 先依分組、再依時間，同組連在一起（LRGB 輪流拍時才不會切得零零碎碎）
    ordered = sorted(decisions, key=lambda d: (d.group, d.metrics.date_obs or "", d.metrics.file))
    x = np.arange(len(ordered))
    keep = np.array([d.keep for d in ordered])
    colors = np.where(keep, theme["keep"], theme["reject"])
    panels = [p for p in PANELS if p != "score" or any(d.score is not None for d in ordered)]

    axes = np.atleast_1d(fig.subplots(len(panels), 1, sharex=True))
    for ax, key in zip(axes, panels):
        ax.set_facecolor(theme["axes"])
        for spine in ax.spines.values():
            spine.set_color(theme["spine"])
        ax.tick_params(colors=theme["muted"], labelcolor=theme["muted"])
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
        ax.scatter(x[keep], y[keep], s=10, c=theme["keep"], label=tr("term.keep"))
        ax.scatter(x[~keep], y[~keep], s=14, c=theme["reject"], marker="x", label=tr("term.reject"))
        ax.set_ylabel(tr(f"plot.{key}"), color=theme["text"])
        ax.grid(color=theme["grid"], linewidth=0.8)
        ax.set_axisbelow(True)
        if key == "score" and thresholds:
            for group, th in thresholds.items():
                idx = [i for i, d in enumerate(ordered) if d.group == group]
                if idx:
                    ax.hlines(th, min(idx) - 0.5, max(idx) + 0.5, colors=theme["threshold"], linestyles="--", lw=1)

    # 換組的地方畫分隔線，並在最上面一格標出組名
    for i in range(len(ordered)):
        if i > 0 and ordered[i].group != ordered[i - 1].group:
            for ax in axes:
                ax.axvline(i - 0.5, color=theme["spine"], lw=0.8, alpha=0.8)
        if ordered[i].group and (i == 0 or ordered[i].group != ordered[i - 1].group):
            axes[0].annotate(ordered[i].group, (i, 1), xycoords=("data", "axes fraction"),
                             xytext=(3, -3), textcoords="offset points", va="top", fontsize=7, color=theme["muted"])

    n_keep = int(keep.sum())
    axes[0].set_title(tr("plot.title", n=len(ordered), keep=n_keep, reject=len(ordered) - n_keep),
                      color=theme["text"])
    legend = axes[0].legend(loc="lower left", fontsize=8, facecolor=theme["legend"], edgecolor=theme["spine"])
    for text in legend.get_texts():
        text.set_color(theme["text"])
    axes[-1].set_xlabel(tr("plot.xlabel"), color=theme["text"])


def plot_decisions(decisions: list[Decision], path: Path, thresholds: dict[str, float] | None = None) -> None:
    """畫好存成 PNG（淺色，方便列印）。"""
    fig = Figure(figsize=(12, 2.2 * len(PANELS)), layout="constrained")
    draw_decisions(fig, decisions, thresholds)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110, facecolor=fig.get_facecolor())
