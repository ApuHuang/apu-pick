"""視窗介面，跟 APU Astro 同一套介面語言：暗房深色主題、頂部列、右側可收合參數面板、底部狀態列。

流程：開啟資料夾 → 量測 → 調門檻（即時更新）→ 搬移淘汰片 / 全部還原。
啟動：python -m astro_light_selector.gui [資料夾]，或打包好的 APUPick（把資料夾拖到 exe 上也可以）。
介面文字都在 i18n.py，可以切換繁體中文 / English。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from PIL import Image, ImageTk

from . import __version__
from .darkroom import (IS_MAC, Darkroom, Fonts, MetricRow, PanelGroup, ParameterSlider, ParameterToggle, Segmented,
                       Tooltip, _short_path, dark_title_bar, enable_dpi_awareness, setup_style)
from .grouping import EXPOSURE_TOLERANCE, assign_nights
from .i18n import APP_NAME, APP_SUBTITLE, LANGUAGES, get_language, set_language, tr, tr_metric
from .metrics import FrameMetrics, is_image
from .mover import read_move_log
from .organize import (DEFAULT_ORGANIZE_KEYS, ORGANIZE_KEYS, ORGANIZE_LOG, organize, pending_undo, plan_organize,
                       undo_organize)
from .pipeline import (REJECT_DIR_NAME, REPORT_NAME, Cancelled, Selection, collect_files, decide, frame_key,
                       measure_files, move_files, reject_dir_for, restore_files, should_recurse)
from .plot import draw_decisions
from .preview import Preview, closeup, make_preview, thumbnail
from .report import (format_duration, kept_exposure, match_frames, read_csv, read_overrides, report_subfolders,
                     score_summary_lines, write_csv)
from .scoring import DEFAULT_WEIGHTS, METRICS, ScoreConfig
from .settings import load_settings, save_settings

ASSETS = Path(__file__).parent / "assets"
ICON = ASSETS / "app.ico"
LOGO = ASSETS / "icon_128.png"
# 開啟資料夾的快捷鍵（說明文字用）；Mac 的 ⌘O 照慣例放在選單列，見 _build_menubar
OPEN_SHORTCUT = "⌘O" if IS_MAC else "Ctrl+O"
# 清單多選時按住的鍵（Mac 的 Control+點是右鍵）
MULTI_SELECT_KEY = "⌘" if IS_MAC else "Ctrl"
# 清單上的快速挑片鍵（K 保留、X 淘汰、A 改回自動）只在沒按修飾鍵時才算：Control、Mac 的 ⌘ / Option、Windows 的 Alt
MODIFIER_MASK = 0x4 | 0x8 | 0x10 if IS_MAC else 0x4 | 0x20000


# 清單欄位：(key, 標題的翻譯代號, 寬度 px, 對齊)
COLUMNS = [
    ("file", "gui.col.file", 330, "w"),
    ("folder", "gui.col.folder", 160, "w"),  # 只在包含子資料夾時顯示
    ("result", "gui.col.result", 60, "center"),
    ("score", "gui.col.score", 55, "e"),
    ("fwhm", "gui.col.fwhm", 55, "e"),
    ("ecc", "gui.col.ecc", 80, "e"),
    ("stars", "gui.col.stars", 60, "e"),
    ("bkg", "gui.col.bkg", 80, "e"),
    ("snr", "gui.col.snr", 55, "e"),
    ("alt", "gui.col.alt", 55, "e"),
    ("moon", "gui.col.moon", 75, "e"),
    ("group", "gui.col.group", 170, "w"),
    ("reason", "gui.col.reason", 360, "w"),
]


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _fmt(v: float | None, spec: str) -> str:
    return "-" if v is None or v != v else format(v, spec)


def _fit(text: str, font, width: int) -> str:
    """文字超過 width 像素就從後面截掉、加「…」。"""
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


# ---------------------------------------------------------------------- 元件


class DarkToolbar(NavigationToolbar2Tk):
    """趨勢圖下面 matplotlib 的工具列，按鈕換成暗房樣式的 ttk 按鈕。

    matplotlib 用 tk.Button：Mac 上 tk.Button 一律畫成系統的淺色按鈕、不理背景色，
    matplotlib 又看背景是深色就把圖示換成白色，白色圖示畫在白色按鈕上就看不見了。
    ttk 按鈕照 clam 主題自己畫，Windows、Mac 長得一樣。
    """

    def _Button(self, text, image_file, toggle, command):  # noqa: N802  覆寫 matplotlib 的方法
        path = Path(image_file)
        large = path.with_name(f"{path.stem}_large.png")
        size = self.winfo_pixels("18p")
        with Image.open(large if size > 24 and large.exists() else path) as im:
            shape = im.convert("RGBA").resize((size, size)).getchannel("A")

        def icon(color: str) -> ImageTk.PhotoImage:
            img = Image.new("RGBA", shape.size, color)
            img.putalpha(shape)
            return ImageTk.PhotoImage(img, master=self)

        images = (icon(Darkroom.label), icon("#5c5c5c"))
        spec = (images[0], "disabled", images[1])
        if toggle:
            button = _ToolToggle(self, image=spec, command=command, style="Pick.Tool.Toolbutton")
        else:
            button = ttk.Button(self, image=spec, command=command, style="Pick.Tool.TButton", takefocus=False)
        button.images = images  # 留著參照，不然圖會被回收
        button.pack(side="left")
        return button


class _ToolToggle(ttk.Checkbutton):
    """平移、放大這種切換鈕；matplotlib 會呼叫 select / deselect 同步狀態。"""

    def __init__(self, master: tk.Misc, **kw: object):
        self.var = tk.IntVar(master=master)
        super().__init__(master, variable=self.var, takefocus=False, **kw)

    def select(self) -> None:
        self.var.set(1)

    def deselect(self) -> None:
        self.var.set(0)


# ---------------------------------------------------------------------- 主視窗


class PickView(tk.Frame):
    """APU Pick 的主畫面。可以單獨放在視窗裡（main），也可以嵌進整合版的分頁。

    不碰整個視窗：標題、大小、選單列、快捷鍵、關閉詢問都由 main() 負責。
    on_language：按下頂部列的語言切換時呼叫（由外面換語言、重建 View 與選單列）；沒給就自己換語言並 rebuild()。
    show_language=False 時頂部列不顯示語言切換。
    """

    def __init__(self, parent: tk.Misc, root: tk.Tk, folder: str | None = None, *,
                 on_language: Callable[[str], None] | None = None, show_language: bool = True):
        super().__init__(parent, bg=Darkroom.canvas)
        self.root = root
        self.on_language = on_language
        self.show_language = show_language
        self.frames: list[FrameMetrics] = []
        self.selection: Selection | None = None
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self._apply_job: str | None = None
        self._sort = ("", False)
        self._started = 0.0
        self.popover: tk.Toplevel | None = None
        self.popover_owner: tk.Widget | None = None
        # 狀態列存成函式，切換語言時可以用新語言重新產生
        self._status_fn: Callable[[], str] = lambda: tr("gui.status.start")

        self.lang_var = tk.StringVar(value=get_language())
        self.method_var = tk.StringVar(value="auto")
        self.pass_var = tk.DoubleVar(value=80)
        self.min_score_var = tk.DoubleVar(value=85)
        self.keep_best_var = tk.DoubleVar(value=70)
        self.group_filter_var = tk.BooleanVar(value=True)
        self.group_exposure_var = tk.BooleanVar(value=True)
        self.group_night_var = tk.BooleanVar(value=False)
        self.group_gain_var = tk.BooleanVar(value=True)
        self.group_folder_var = tk.BooleanVar(value=False)
        # 包含子資料夾：開資料夾時自動判斷（本身沒影像、子資料夾有；或上次的報表有子資料夾），也可以自己切換
        self.recursive_var = tk.BooleanVar(value=False)
        self._setting_folder = False
        self.exposure_tol_var = tk.DoubleVar(value=float(load_settings().get("exposure_tolerance", EXPOSURE_TOLERANCE)))
        self.workers_var = tk.IntVar(value=default_workers())
        # 監看資料夾：拍攝時定時檢查，新檔案寫完就量測；每次開資料夾都從關閉開始
        self.watch_var = tk.BooleanVar(value=False)
        self._watch_job: str | None = None
        self._watch_sizes: dict[str, int] = {}
        self.only_reject_var = tk.BooleanVar(value=False)
        # 整理檔案：依哪些參數分資料夾（記住上次勾的）
        organize_keys = load_settings().get("organize_keys") or list(DEFAULT_ORGANIZE_KEYS)
        self.organize_vars = {k: tk.BooleanVar(value=k in organize_keys) for k in ORGANIZE_KEYS}
        # 趨勢圖只在看得到時才畫（清單上連按 K / X 時不用每次重畫）；_plot_order 是圖上第 i 個點是哪一張
        self._plot_dirty = False
        self._plot_order: list = []
        # 使用者在清單上手動覆寫的結果：{檔名: "keep" / "reject"}，存在報表的 override 欄
        self.overrides: dict[str, str] = {}
        # 預覽：最近看過的幾張留在記憶體，上下鍵切換比較快
        self._previews: OrderedDict[str, Preview] = OrderedDict()
        self._preview_name: str | None = None
        self._preview_center = (0.5, 0.5)
        self._preview_job: str | None = None
        self.folder_var = tk.StringVar()
        self.reject_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.summary_var = tk.StringVar()
        settings = load_settings()
        self.panel_state: dict[str, bool] = dict(settings.get("panel", {}))
        # 權重滑桿存 0~100 的相對值；上次自訂過就沿用
        saved = settings.get("weights") or {}
        self.weight_vars = {m: tk.DoubleVar(value=float(saved.get(m, DEFAULT_WEIGHTS[m] * 100)))
                            for m in METRICS}

        self._scale = root.winfo_fpixels("1i") / 96.0
        self.fonts = Fonts(root)
        self._setup_style()
        # 這個 View 專用的事件標籤：加在自己底下每個元件上，不用 bind_all，
        # 跟別的畫面放在同一個視窗時（整合版的分頁）不會互相搶事件
        self._tag = f"ApuPickView{id(self)}"
        self.bind_class(self._tag, "<Button-1>", self._maybe_close_popover, add="+")
        self.bind_class(self._tag, "<Escape>", lambda _e: self.close_popover())
        self.bind_class(self._tag, "<MouseWheel>", self._scroll_panel, add="+")
        self._build()
        for var in (self.method_var, self.pass_var, self.min_score_var, self.keep_best_var,
                    self.group_filter_var, self.group_exposure_var, self.group_night_var,
                    self.group_gain_var, self.group_folder_var,
                    self.exposure_tol_var, *self.weight_vars.values()):
            var.trace_add("write", lambda *_: self._schedule_apply())
        self.recursive_var.trace_add("write", lambda *_: self._recursive_changed())
        for var in self.weight_vars.values():
            var.trace_add("write", lambda *_: self._update_weight_view())
        self.only_reject_var.trace_add("write", lambda *_: self._fill_table())
        for var in self.organize_vars.values():
            var.trace_add("write", lambda *_: save_settings(organize_keys=list(self._organize_keys())))
        self.watch_var.trace_add("write", lambda *_: self._toggle_watch())
        # 延到事件處理完再換：換語言會重建介面，包含正在處理點擊的那個切換鈕
        self.lang_var.trace_add("write", lambda *_: self.after_idle(self._language_clicked))
        self._refresh_all()
        self._poll_job: str | None = self.after(100, self._poll)
        if folder:
            self._set_folder(Path(folder))

    def px(self, v: float) -> int:
        return int(round(v * self._scale))

    # ------------------------------------------------------------------ 樣式與版面

    def _setup_style(self) -> None:
        setup_style(self.root, self.px, self.fonts)
        D = Darkroom
        style = ttk.Style(self.root)
        # 趨勢圖工具列的圖示按鈕：平的，滑過去才有底色，切換鈕按下去維持亮一階
        tool_colors = [("disabled", D.chrome), ("pressed", D.segment_on), ("selected", D.segment_on),
                       ("active", D.hover)]
        for name in ("Pick.Tool.TButton", "Pick.Tool.Toolbutton"):
            style.configure(name, background=D.chrome, bordercolor=D.chrome, lightcolor=D.chrome,
                            darkcolor=D.chrome, focuscolor=D.chrome, relief="flat", padding=self.px(3))
            style.map(name, background=tool_colors, bordercolor=tool_colors, lightcolor=tool_colors,
                      darkcolor=tool_colors)

    def _build(self) -> None:
        self._build_top_bar()
        self._build_status_bar()
        body = tk.Frame(self, bg=Darkroom.canvas)
        body.pack(fill="both", expand=True)
        self._build_panel(body)
        tk.Frame(body, bg=Darkroom.separator, width=1).pack(side="right", fill="y")
        self._build_canvas_area(body)
        self._tag_widgets(self)

    def _tag_widgets(self, widget: tk.Misc) -> None:
        """把這個 View 的事件標籤加到自己和底下每個元件（放在 'all' 之前；重建介面後要再做一次）。"""
        tags = list(widget.bindtags())
        if self._tag not in tags:
            tags.insert(max(0, len(tags) - 1), self._tag)
            widget.bindtags(tuple(tags))
        for child in widget.winfo_children():
            if not isinstance(child, tk.Toplevel):
                self._tag_widgets(child)

    def _text_target(self) -> tk.Misc:
        """拷貝、全選的對象：門檻細節的文字是唯讀的，點了不會拿到焦點，所以沒有焦點時就是它。"""
        widget = self.root.focus_get()
        return widget if isinstance(widget, tk.Text) and str(widget).startswith(str(self)) else self.summary_text

    def copy(self) -> None:
        """選單「編輯 → 拷貝」。"""
        self._text_target().event_generate("<<Copy>>")

    def select_all(self) -> None:
        """選單「編輯 → 全選」。"""
        self._text_target().event_generate("<<SelectAll>>")

    def _build_top_bar(self) -> None:
        D = Darkroom
        bar = tk.Frame(self, bg=D.chrome, height=self.px(D.top_bar_height))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(self, bg=D.separator, height=1).pack(fill="x")

        identity = tk.Frame(bar, bg=D.chrome)
        identity.pack(side="left", padx=(self.px(14), 0))
        tk.Label(identity, text=APP_NAME, font=self.fonts.title, fg=D.label, bg=D.chrome).pack(side="left")
        badge = tk.Frame(identity, bg=D.beta, padx=1, pady=1)  # 1px 橘色外框
        badge.pack(side="left", padx=(self.px(7), 0))
        tk.Label(badge, text=f"v{__version__}", font=self.fonts.badge, fg=D.beta, bg=D.chrome,
                 padx=self.px(4)).pack()

        actions = tk.Frame(bar, bg=D.chrome)
        actions.pack(side="right", padx=(0, self.px(14)))
        if self.show_language:
            Segmented(actions, self, [("zh", "繁中"), ("en", "EN")], self.lang_var).pack(side="left")
            tk.Frame(actions, bg=D.separator, width=1, height=self.px(18)).pack(side="left", padx=self.px(10))
        self.open_btn = ttk.Button(actions, text=tr("gui.btn.open"), style="Dark.TButton",
                                   command=self._browse_folder)
        self.open_btn.pack(side="left")
        Tooltip(self.open_btn, tr("gui.btn.open.help", shortcut=OPEN_SHORTCUT), self)
        self.measure_btn = ttk.Button(actions, style="Dark.TButton", command=self._measure_or_stop)
        self.measure_btn.pack(side="left", padx=(self.px(6), 0))
        Tooltip(self.measure_btn, tr("gui.btn.measure.help"), self)
        self.move_btn = ttk.Button(actions, text=tr("gui.btn.move"), style="Prominent.TButton", command=self._move)
        self.move_btn.pack(side="left", padx=(self.px(6), 0))
        Tooltip(self.move_btn, tr("gui.btn.move.help"), self)

        # 中間：目前的資料夾名稱，對應 APU Astro 的檔名
        self.folder_title = tk.Label(bar, font=self.fonts.ui, bg=D.chrome)
        self.folder_title.pack(side="left", expand=True, padx=self.px(16))

    def _build_status_bar(self) -> None:
        D = Darkroom
        bar = tk.Frame(self, bg=D.chrome, height=self.px(D.status_bar_height))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self, bg=D.separator, height=1).pack(side="bottom", fill="x")
        self.indicator = tk.Frame(bar, bg=D.chrome)
        self.indicator.pack(side="left", padx=(self.px(14), 0))
        self.status_dot = tk.Canvas(self.indicator, width=self.px(7), height=self.px(7), bg=D.chrome,
                                    highlightthickness=0)
        self.progress = ttk.Progressbar(self.indicator, style="Dark.Horizontal.TProgressbar", length=self.px(120),
                                        mode="determinate")
        tk.Label(bar, textvariable=self.summary_var, font=self.fonts.small, fg=D.label, bg=D.chrome
                 ).pack(side="right", padx=self.px(14))
        tk.Label(bar, textvariable=self.status_var, font=self.fonts.small, fg=D.secondary, bg=D.chrome,
                 anchor="w").pack(side="left", fill="x", expand=True, padx=self.px(8))

    def _build_panel(self, body: tk.Frame) -> None:
        D = Darkroom
        outer = tk.Frame(body, bg=D.panel, width=self.px(D.panel_width))
        outer.pack(side="right", fill="y")
        outer.pack_propagate(False)
        # 分組一多，矮螢幕放不下：內容放在可捲動的 Canvas 裡，超出高度才出現捲軸
        canvas = tk.Canvas(outer, bg=D.panel, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        panel = tk.Frame(canvas, bg=D.panel)
        window = canvas.create_window((0, 0), window=panel, anchor="nw")
        self.panel_canvas = canvas

        def relayout(_event: object = None) -> None:
            canvas.itemconfigure(window, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, 0, panel.winfo_reqheight()))
            if panel.winfo_reqheight() > canvas.winfo_height() > 1:
                scrollbar.pack(side="right", fill="y", before=canvas)
            else:
                scrollbar.pack_forget()
                canvas.yview_moveto(0)

        panel.bind("<Configure>", relayout)
        canvas.bind("<Configure>", relayout)
        self._relayout_panel = relayout

        group = PanelGroup(panel, self, "result", tr("gui.group.result"), tr("gui.group.result.info"))
        self.metrics = {key: MetricRow(group.body, self, tr(f"gui.metric.{key}"))
                        for key in ("frames", "kept", "rejected", "manual", "threshold", "ref_fwhm", "kept_exposure")}
        # 保留片總曝光：超過一組、一晚時，每組、每晚各一行
        self.exposure_rows = tk.Frame(group.body, bg=D.panel)
        self.exposure_rows.pack(fill="x")

        group = PanelGroup(panel, self, "threshold", tr("gui.group.threshold"), tr("gui.group.threshold.info"))
        Segmented(group.body, self, [("auto", tr("gui.method.auto")), ("min_score", tr("gui.method.min_score")),
                                     ("keep_best", tr("gui.method.keep_best"))],
                  self.method_var, stretch=True).pack(fill="x", pady=(0, self.px(8)))
        points = lambda v: tr("gui.value.points", v=v)  # noqa: E731
        self.sliders = {
            "auto": ParameterSlider(group.body, self, tr("gui.slider.pass_line"), self.pass_var, 50, 100, points),
            "min_score": ParameterSlider(group.body, self, tr("gui.slider.min_score"), self.min_score_var,
                                         0, 100, points),
            "keep_best": ParameterSlider(group.body, self, tr("gui.slider.keep_best"), self.keep_best_var,
                                         10, 100, lambda v: f"{v:.0f}%"),
        }

        group = PanelGroup(panel, self, "weights", tr("gui.group.weights"), tr("gui.group.weights.info"))
        row = tk.Frame(group.body, bg=D.panel)
        row.pack(fill="x", pady=(0, self.px(6)))
        self.weight_badge = tk.Label(row, font=self.fonts.small, bg=D.panel)
        self.weight_badge.pack(side="left")
        self.reset_weights_btn = ttk.Button(row, text=tr("gui.btn.reset_weights"), style="Dark.TButton",
                                            command=self._reset_weights)
        self.reset_weights_btn.pack(side="right")
        # 右邊顯示換算後的百分比；四個滑桿互相影響，所以由 _update_weight_view 一起更新
        self.weight_sliders = {
            m: ParameterSlider(group.body, self, tr(key), self.weight_vars[m], 0, 100,
                               lambda _v, m=m: self._weight_share_text(m),
                               edit_text=lambda m=m: self._weight_share_number(m),
                               on_entry=lambda v, m=m: self._set_weight_share(m, v))
            for m, key in (("fwhm", "gui.col.fwhm"), ("eccentricity", "gui.col.ecc"),
                           ("n_stars", "gui.col.stars"), ("background", "gui.col.bkg"), ("snr", "gui.col.snr"))
        }
        for slider in self.weight_sliders.values():
            slider.pack(fill="x", pady=(0, self.px(4)))

        group = PanelGroup(panel, self, "grouping", tr("gui.group.grouping"), tr("gui.group.grouping.info"))
        for key, var in (("filter", self.group_filter_var), ("exposure", self.group_exposure_var),
                         ("gain", self.group_gain_var), ("night", self.group_night_var),
                         ("folder", self.group_folder_var)):
            ParameterToggle(group.body, self, tr(f"gui.toggle.{key}"), var).pack(fill="x", pady=self.px(2))
        ParameterSlider(group.body, self, tr("gui.slider.exposure_tolerance"), self.exposure_tol_var, 0, 30,
                        lambda v: tr("gui.value.seconds", v=v)).pack(fill="x", pady=(self.px(6), 0))

        group = PanelGroup(panel, self, "measure", tr("gui.group.measure"), tr("gui.group.measure.info"))
        ParameterSlider(group.body, self, tr("gui.slider.workers"), self.workers_var, 1,
                        max(2, os.cpu_count() or 2), lambda v: f"{v:.0f}").pack(fill="x")
        ParameterToggle(group.body, self, tr("gui.toggle.recursive"), self.recursive_var).pack(
            fill="x", pady=(self.px(6), 0))
        ParameterToggle(group.body, self, tr("gui.toggle.watch"), self.watch_var).pack(fill="x", pady=(self.px(2), 0))
        self.load_btn = ttk.Button(group.body, text=tr("gui.btn.load"), style="Dark.TButton",
                                   command=self._load_report)
        self.load_btn.pack(anchor="w", pady=(self.px(10), 0))
        group = PanelGroup(panel, self, "rejects", tr("gui.group.rejects"), tr("gui.group.rejects.info"))
        tk.Label(group.body, text=tr("gui.reject_folder"), font=self.fonts.small, fg=D.secondary,
                 bg=D.panel).pack(anchor="w")
        self.reject_label = tk.Label(group.body, font=self.fonts.small, fg=D.label, bg=D.panel, anchor="w",
                                     justify="left")
        self.reject_label.pack(fill="x", pady=(self.px(2), self.px(8)))
        row = tk.Frame(group.body, bg=D.panel)
        row.pack(fill="x")
        self.change_btn = ttk.Button(row, text=tr("gui.btn.change"), style="Dark.TButton",
                                     command=self._browse_reject)
        self.change_btn.pack(side="left")
        self.default_reject_btn = ttk.Button(row, text=tr("gui.btn.default_reject"), style="Dark.TButton",
                                             command=lambda: self._set_reject(None))
        self.default_reject_btn.pack(side="left", padx=(self.px(6), 0))
        self.restore_btn = ttk.Button(group.body, text=tr("gui.btn.restore"), style="Dark.TButton",
                                      command=self._restore)
        self.restore_btn.pack(anchor="w", pady=(self.px(6), 0))

        group = PanelGroup(panel, self, "organize", tr("gui.group.organize"), tr("gui.group.organize.info"))
        for key in ORGANIZE_KEYS:
            ParameterToggle(group.body, self, tr(f"gui.organize.{key}"), self.organize_vars[key]).pack(
                fill="x", pady=self.px(2))
        row = tk.Frame(group.body, bg=D.panel)
        row.pack(fill="x", pady=(self.px(8), 0))
        self.organize_btn = ttk.Button(row, text=tr("gui.btn.organize"), style="Dark.TButton", command=self._organize)
        self.organize_btn.pack(side="left")
        self.undo_organize_btn = ttk.Button(row, text=tr("gui.btn.undo_organize"), style="Dark.TButton",
                                            command=self._undo_organize)
        self.undo_organize_btn.pack(side="left", padx=(self.px(6), 0))

    def _build_canvas_area(self, body: tk.Frame) -> None:
        D = Darkroom
        area = tk.Frame(body, bg=D.canvas)
        area.pack(side="left", fill="both", expand=True)

        # 還沒有結果時的歡迎畫面：Logo、名稱、副標與下一步提示
        self.welcome = tk.Frame(area, bg=D.canvas)
        hero = tk.Frame(self.welcome, bg=D.canvas)
        hero.place(relx=0.5, rely=0.45, anchor="center")
        if LOGO.is_file():
            try:
                self._logo = tk.PhotoImage(file=str(LOGO))
                tk.Label(hero, image=self._logo, bg=D.canvas).pack(pady=(0, self.px(14)))
            except tk.TclError:
                pass
        tk.Label(hero, text=APP_NAME, font=self.fonts.hero, fg=D.label, bg=D.canvas).pack()
        tk.Label(hero, text=APP_SUBTITLE, font=self.fonts.hero_sub, fg=D.secondary, bg=D.canvas).pack()
        self.welcome_hint = tk.Label(hero, font=self.fonts.ui, fg=D.accent, bg=D.canvas)
        self.welcome_hint.pack(pady=(self.px(22), 0))

        self.nb = ttk.Notebook(area, style="Dark.TNotebook")
        plot_tab = ttk.Frame(self.nb, style="Canvas.TFrame")
        self.nb.add(plot_tab, text=tr("gui.tab.plot"))
        self.fig = Figure(figsize=(10, 6), layout="constrained", facecolor=D.canvas)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_tab)
        toolbar = DarkToolbar(self.canvas, plot_tab, pack_toolbar=False)
        toolbar.configure(background=D.chrome)
        for child in toolbar.winfo_children():
            try:
                child.configure(background=D.separator if isinstance(child, tk.Frame) else D.chrome)
                if isinstance(child, tk.Label):
                    child.configure(foreground=D.secondary)
            except tk.TclError:
                pass
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        self.toolbar = toolbar
        self.canvas.get_tk_widget().configure(bg=D.canvas, highlightthickness=0)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._draw_plot())

        list_tab = ttk.Frame(self.nb, style="Canvas.TFrame")
        self.nb.add(list_tab, text=tr("gui.tab.list"))
        top = tk.Frame(list_tab, bg=D.canvas)
        top.pack(fill="x", padx=self.px(8), pady=self.px(6))
        ParameterToggle(top, self, tr("gui.only_reject"), self.only_reject_var, bg=D.canvas).pack(side="left")
        self.override_btns = []
        for key, choice in (("gui.btn.clear_override", None), ("gui.btn.force_reject", "reject"),
                            ("gui.btn.force_keep", "keep")):
            btn = ttk.Button(top, text=tr(key), style="Dark.TButton",
                             command=lambda c=choice: self._set_override(c))
            btn.pack(side="right", padx=(self.px(6), 0))
            Tooltip(btn, tr("gui.override.help", shortcut=MULTI_SELECT_KEY), self)
            self.override_btns.append(btn)
        self.row_menu = tk.Menu(self, tearoff=False, bg=D.group_header, fg=D.label,
                                activebackground=D.prominent, activeforeground="white", bd=0)
        for key, choice in (("gui.btn.force_keep", "keep"), ("gui.btn.force_reject", "reject"),
                            ("gui.btn.clear_override", None)):
            self.row_menu.add_command(label=tr(key), command=lambda c=choice: self._set_override(c))
        split = tk.Frame(list_tab, bg=D.canvas)
        split.pack(fill="both", expand=True)
        self._build_preview_pane(split)
        table = tk.Frame(split, bg=D.canvas)
        table.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in COLUMNS], show="headings", style="Dark.Treeview")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=tr(title), command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=self.px(width), anchor=anchor, stretch=key in ("file", "reason"))
        self.tree.tag_configure("reject", foreground=D.reject)
        # 右鍵：Windows / Linux 是 Button-3，macOS 觸控板是 Button-2 或 Control-點
        for sequence in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.tree.bind(sequence, self._show_row_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._schedule_preview())
        # 快速挑片：K 強制保留、X 強制淘汰、A 改回自動
        for letter, choice in (("k", "keep"), ("x", "reject"), ("a", None)):
            for key in (letter, letter.upper()):
                self.tree.bind(f"<KeyPress-{key}>", lambda e, c=choice: self._key_override(e, c))
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        xs = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview, style="Dark.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)

        details_tab = ttk.Frame(self.nb, style="Canvas.TFrame")
        self.nb.add(details_tab, text=tr("gui.tab.details"))
        self.summary_text = tk.Text(details_tab, wrap="word", relief="flat", bg=D.list_bg, fg=D.label,
                                    font=self.fonts.ui, padx=self.px(16), pady=self.px(12), highlightthickness=0,
                                    insertbackground=D.label)
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.configure(state="disabled")

    def _scroll_panel(self, event: tk.Event) -> None:
        """滑鼠在右側面板上時，滾輪捲動面板（其他地方的滾輪照原本的行為）。"""
        canvas = self.panel_canvas
        if not canvas.winfo_exists():
            return
        widget = event.widget if isinstance(event.widget, tk.Misc) else None
        if widget is None or not str(widget).startswith(str(canvas)):
            return
        if canvas.yview() == (0.0, 1.0):
            return
        # Windows 一格是 120，macOS 是 ±1 起跳的小數字
        steps = -int(event.delta / 120) if abs(event.delta) >= 120 else -event.delta
        canvas.yview_scroll(steps, "units")

    # ------------------------------------------------------------------ 預覽

    def _build_preview_pane(self, parent: tk.Widget) -> None:
        D = Darkroom
        pane = tk.Frame(parent, bg=D.list_bg, width=self.px(440))
        pane.pack(side="right", fill="y")
        pane.pack_propagate(False)
        tk.Frame(parent, bg=D.separator, width=1).pack(side="right", fill="y")
        self.preview_pane = pane
        self.preview_title = tk.Label(pane, font=self.fonts.small, fg=D.label, bg=D.list_bg, anchor="w")
        self.preview_title.pack(fill="x", padx=self.px(10), pady=(self.px(8), 0))
        self.preview_sky = tk.Label(pane, font=self.fonts.small, fg=D.secondary, bg=D.list_bg, anchor="w")
        self.preview_sky.pack(fill="x", padx=self.px(10), pady=(0, self.px(4)))
        self.preview_thumb = tk.Label(pane, bg=D.list_bg, fg=D.secondary, font=self.fonts.small,
                                      text=tr("gui.preview.hint"), cursor="crosshair")
        self.preview_thumb.pack(padx=self.px(10))
        self.preview_thumb.bind("<Button-1>", self._move_closeup)
        self.preview_caption = tk.Label(pane, font=self.fonts.small, fg=D.secondary, bg=D.list_bg, anchor="w")
        self.preview_caption.pack(fill="x", padx=self.px(10), pady=(self.px(8), self.px(4)))
        self.preview_closeup = tk.Label(pane, bg=D.list_bg)
        self.preview_closeup.pack(padx=self.px(10))

    def _schedule_preview(self) -> None:
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(120, self._load_preview)

    def _current_path(self, key: str) -> Path | None:
        """這張片（key 是相對於開啟資料夾的路徑）現在在哪：還在 light 資料夾，或已經被搬到淘汰片資料夾。"""
        folder = self._folder()
        if folder is None:
            return None
        home = folder / key
        if home.exists():
            return home
        moved = read_move_log(reject_dir_for(home.parent, folder, self._reject_dir(), self._recursive()))
        target = moved.get(home.resolve())
        return target if target is not None and target.exists() else None

    def _load_preview(self) -> None:
        self._preview_job = None
        selected = self.tree.selection()
        if not selected:
            return
        name = selected[0]
        self._preview_name = name
        self.preview_title.configure(text=name)
        self.preview_sky.configure(text=self._sky_text(name))
        if name in self._previews:
            self._previews.move_to_end(name)
            self._show_preview()
            return
        path = self._current_path(name)
        if path is None:
            self._clear_preview(tr("gui.preview.missing"))
            return
        self._clear_preview(tr("gui.preview.loading"))

        def work() -> None:
            try:
                self.events.put(("preview", name, make_preview(path)))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("preview_error", name, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _sky_text(self, key: str) -> str:
        """拍攝當下的天空，一行字；header 沒有座標、地點時是空的。"""
        folder = self._folder()
        m = next((d.metrics for d in self.selection.decisions if frame_key(d.metrics.file, folder) == key), None) \
            if self.selection else None
        if m is None or m.altitude is None:
            return ""
        illum = (m.moon_illum or 0) * 100
        if m.moon_alt is not None and m.moon_alt > 0:
            return tr("gui.preview.sky_moon_up", alt=m.altitude, moon=m.moon_alt, illum=illum, sep=m.moon_sep or 0)
        return tr("gui.preview.sky_moon_down", alt=m.altitude, illum=illum)

    def _clear_preview(self, text: str) -> None:
        self.preview_thumb.configure(image="", text=text)
        self.preview_closeup.configure(image="")
        self.preview_caption.configure(text="")

    def _show_preview(self) -> None:
        preview = self._previews.get(self._preview_name or "")
        if preview is None:
            return
        width = max(self.px(200), self.preview_pane.winfo_width() - self.px(20))
        thumb = thumbnail(preview, width, self.px(300))
        self._thumb_image = ImageTk.PhotoImage(thumb, master=self.root)
        self.preview_thumb.configure(image=self._thumb_image, text="")
        cells = max(40, width // 2)
        self._closeup_image = ImageTk.PhotoImage(closeup(preview, *self._preview_center, cells, 2),
                                                 master=self.root)
        self.preview_closeup.configure(image=self._closeup_image)
        self.preview_caption.configure(text=tr("gui.preview.closeup"))

    def _move_closeup(self, event: tk.Event) -> None:
        image = getattr(self, "_thumb_image", None)
        if image is None or self._preview_name not in self._previews:
            return
        # Label 把圖置中，換算成圖上的相對位置
        x = (event.x - (self.preview_thumb.winfo_width() - image.width()) / 2) / image.width()
        y = (event.y - (self.preview_thumb.winfo_height() - image.height()) / 2) / image.height()
        self._preview_center = (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))
        self._show_preview()

    # ------------------------------------------------------------------ 說明氣泡

    def show_popover(self, owner: tk.Widget, text: str) -> None:
        D = Darkroom
        self.close_popover()
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.configure(bg=D.separator)
        tk.Label(top, text=text, font=self.fonts.ui, fg=D.label, bg=D.group_header, justify="left",
                 wraplength=self.px(360), padx=self.px(16), pady=self.px(14)).pack(padx=1, pady=1)
        top.update_idletasks()
        # 面板在右邊，說明往左邊開
        x = owner.winfo_rootx() - top.winfo_reqwidth() - self.px(8)
        y = owner.winfo_rooty() - self.px(6)
        top.geometry(f"+{max(x, self.root.winfo_rootx())}+{y}")
        self.popover, self.popover_owner = top, owner

    def close_popover(self) -> None:
        if self.popover is not None:
            try:
                self.popover.destroy()
            except tk.TclError:
                pass
        self.popover, self.popover_owner = None, None

    def _maybe_close_popover(self, event: tk.Event) -> None:
        if self.popover is None or event.widget is self.popover_owner:
            return
        if str(event.widget).startswith(str(self.popover)):
            return
        self.close_popover()

    # ------------------------------------------------------------------ 語言

    def _set_language(self, lang: str) -> None:
        self.lang_var.set(lang)

    def _language_clicked(self) -> None:
        lang = self.lang_var.get()
        if lang not in LANGUAGES or lang == get_language():
            return
        if self.is_busy():  # 量測中不換：重建介面會打斷進度顯示
            self.lang_var.set(get_language())
            return
        if self.on_language is not None:
            self.on_language(lang)
        else:
            set_language(lang)
            save_settings(language=lang)
            self.rebuild()

    def rebuild(self) -> None:
        """照目前語言重建自己的介面，已經量好的結果、設定都保留。"""
        if self.lang_var.get() != get_language():
            self.lang_var.set(get_language())
        self.close_popover()
        for child in self.winfo_children():
            child.destroy()
        self._build()
        if self.frames:
            self._apply()
        self._refresh_all()
        # Mac：在已經顯示的視窗裡重建，Canvas 裡的面板要等整個畫面排完再排一次才會畫出來，不然整片空白
        self.update_idletasks()
        self._relayout_panel()

    # ------------------------------------------------------------------ 狀態

    def _status(self, fn: Callable[[], str]) -> None:
        self._status_fn = fn
        self.status_var.set(fn())

    def _folder(self) -> Path | None:
        text = self.folder_var.get().strip().strip('"')
        return Path(text) if text else None

    def _reject_dir(self) -> Path | None:
        """自訂的淘汰片資料夾；沒自訂時，只開一層是 <資料夾>/rejected，子資料夾模式是 None（各資料夾裡的 rejected）。"""
        folder = self._folder()
        text = self.reject_var.get().strip().strip('"')
        if text:
            return Path(text)
        if self._recursive():
            return None
        return folder / REJECT_DIR_NAME if folder else None

    def _recursive(self) -> bool:
        """包含子資料夾：淘汰片預設搬到每張自己所在資料夾的 rejected；自訂時照子資料夾結構放進去。"""
        return bool(self.recursive_var.get())

    def is_busy(self) -> bool:
        """有量測在跑（關閉視窗前要詢問）。"""
        return self.worker is not None and self.worker.is_alive()

    def _refresh_all(self) -> None:
        """依目前狀態更新所有跟語言、資料有關的顯示。"""
        self.status_var.set(self._status_fn())
        self._update_buttons()
        self._update_method_state()
        self._update_weight_view()
        self._update_result_view()

    def _update_buttons(self) -> None:
        busy = self.is_busy()
        folder = self._folder()
        has_folder = folder is not None and folder.is_dir()
        idle = "disabled" if busy else "!disabled"
        self.open_btn.state([idle])
        if busy:
            self.measure_btn.configure(text=tr("gui.btn.stop"))
            self.measure_btn.state(["!disabled"])
        else:
            self.measure_btn.configure(text=tr("gui.btn.remeasure") if self.frames else tr("gui.btn.measure"))
            self.measure_btn.state(["!disabled" if has_folder else "disabled"])
        self.move_btn.state([idle if self.selection is not None else "disabled"])
        has_report = has_folder and (folder / REPORT_NAME).is_file()
        self.load_btn.state([idle if has_report else "disabled"])
        self.restore_btn.state([idle if has_folder else "disabled"])
        self.change_btn.state([idle if has_folder else "disabled"])
        custom = self.reject_var.get().strip()
        self.default_reject_btn.state([idle if has_folder and custom else "disabled"])
        self.organize_btn.state([idle if self.frames and has_folder else "disabled"])
        self.undo_organize_btn.state([idle if has_folder and (folder / ORGANIZE_LOG).is_file() else "disabled"])

        D = Darkroom
        self.folder_title.configure(text=folder.name if folder else tr("gui.no_folder"),
                                    fg=D.label if folder else D.secondary)
        reject_dir = self._reject_dir()
        if self._recursive():
            self.reject_label.configure(text=f"{_short_path(custom)}\n{tr('gui.reject_mirror')}" if custom
                                        else tr("gui.reject_each"))
        else:
            self.reject_label.configure(text=_short_path(str(reject_dir)) if reject_dir else "—")
        if busy:
            self.status_dot.pack_forget()
            self.progress.pack(side="left", pady=self.px(9))
        else:
            self.progress.pack_forget()
            self.status_dot.pack(side="left")
            self.status_dot.delete("all")
            color = D.accent if self.selection is not None else D.secondary
            self.status_dot.create_oval(0, 0, self.px(6), self.px(6), fill=color, outline=color)
        if busy:
            hint = tr("gui.welcome.measuring")
        elif has_folder:
            hint = tr("gui.welcome.ready")
        else:
            hint = tr("gui.welcome.hint")
        self.welcome_hint.configure(text=hint)

    def _weights(self) -> dict[str, float] | None:
        """目前的權重（相對值）；讀不到或全部是 0 時回傳 None。"""
        try:
            weights = {m: max(0.0, float(var.get())) for m, var in self.weight_vars.items()}
        except (tk.TclError, ValueError):
            return None
        return weights if sum(weights.values()) > 0 else None

    def _weights_are_recommended(self, weights: dict[str, float]) -> bool:
        total = sum(weights.values())
        return all(abs(weights[m] / total - DEFAULT_WEIGHTS[m]) < 0.005 for m in METRICS)

    def _weight_share_text(self, metric: str) -> str:
        weights = self._weights()
        return f"{weights[metric] / sum(weights.values()) * 100:.0f}%" if weights else "—"

    def _weight_share_number(self, metric: str) -> str:
        weights = self._weights()
        return f"{weights[metric] / sum(weights.values()) * 100:.0f}" if weights else ""

    def _set_weight_share(self, metric: str, pct: float) -> None:
        """在權重滑桿上直接打百分比：調這一項讓它佔 pct%，其他幾項維持彼此的比例。"""
        pct = min(max(pct, 0.0), 100.0)
        try:
            weights = {m: max(0.0, float(var.get())) for m, var in self.weight_vars.items()}
        except (tk.TclError, ValueError):
            return
        others = sum(w for m, w in weights.items() if m != metric)
        if pct >= 100:
            weights = {m: 100.0 if m == metric else 0.0 for m in weights}
        elif others <= 0:
            weights[metric] = 100.0 if pct > 0 else 0.0
        else:
            weights[metric] = pct / (100 - pct) * others
        peak = max(weights.values())
        if peak > 100:  # 滑桿最多 100：整組等比例縮小，百分比不變
            weights = {m: w * 100 / peak for m, w in weights.items()}
        for m, w in weights.items():
            self.weight_vars[m].set(round(w, 2))

    def _update_weight_view(self) -> None:
        """每個滑桿右邊顯示換算後的百分比，上面標示建議 / 自訂。"""
        weights = self._weights()
        for m, slider in self.weight_sliders.items():
            slider.value.configure(text=self._weight_share_text(m))
        recommended = weights is not None and self._weights_are_recommended(weights)
        self.weight_badge.configure(
            text=tr("gui.weights.recommended") if recommended else tr("gui.weights.custom"),
            fg=Darkroom.accent if recommended else Darkroom.beta)
        self.reset_weights_btn.state(["disabled" if recommended else "!disabled"])

    def _reset_weights(self) -> None:
        for m, var in self.weight_vars.items():
            var.set(DEFAULT_WEIGHTS[m] * 100)

    def _update_method_state(self) -> None:
        method = self.method_var.get()
        for key, slider in self.sliders.items():
            if key == method:
                slider.pack(fill="x")
            else:
                slider.pack_forget()

    def _update_result_view(self) -> None:
        """有結果顯示分頁，沒有就顯示歡迎畫面。"""
        if self.selection is not None:
            self.welcome.pack_forget()
            self.nb.pack(fill="both", expand=True)
        else:
            self.nb.pack_forget()
            self.welcome.pack(fill="both", expand=True)
            for row in self.metrics.values():
                row.set("—")
            for child in self.exposure_rows.winfo_children():
                child.destroy()
            self.summary_var.set("")

    # ------------------------------------------------------------------ 資料夾

    def _browse_folder(self) -> None:
        if self.is_busy():
            return
        initial = self._folder()
        path = filedialog.askdirectory(title=tr("gui.pick_folder"),
                                       initialdir=str(initial) if initial and initial.is_dir() else None)
        if path:
            self._set_folder(Path(path))

    def _browse_reject(self) -> None:
        path = filedialog.askdirectory(title=tr("gui.pick_reject"))
        if path:
            self._set_reject(Path(path))

    def _set_reject(self, path: Path | None) -> None:
        """自訂淘汰片資料夾（None 改回預設）；每個資料夾分開記住，下次開同一個資料夾才找得到搬過去的片。"""
        self.reject_var.set(str(path) if path else "")
        folder = self._folder()
        if folder is not None:
            saved = dict(load_settings().get("reject_dirs") or {})
            if path:
                saved[str(folder.resolve())] = str(path)
            else:
                saved.pop(str(folder.resolve()), None)
            save_settings(reject_dirs=saved)
        self._update_buttons()

    def open_folder(self, path: str | Path) -> None:
        """開檔入口：開啟放 light frames 的資料夾（拖一張影像進來也行）。"""
        self._set_folder(Path(path))

    def ask_open(self) -> None:
        """選單「開啟資料夾…」與快捷鍵：跳出選資料夾的對話框。"""
        self._browse_folder()

    def _set_folder(self, folder: Path, recursive: bool | None = None) -> None:
        """開啟資料夾。recursive=None 時自動判斷要不要包含子資料夾。"""
        if folder.is_file() and is_image(folder):
            folder = folder.parent  # 拖一張 FITS 進來也行
        if recursive is None:
            # 報表是子資料夾模式存的、而且那些子資料夾還在；檔案被搬回同一層時照資料夾現在的樣子判斷
            recursive = folder.is_dir() and (report_subfolders(folder / REPORT_NAME, folder) or should_recurse(folder))
        self._setting_folder = True
        try:
            self.recursive_var.set(recursive)
        finally:
            self._setting_folder = False
        self.watch_var.set(False)  # 換資料夾就停止監看，要監看新的資料夾再自己打開
        self.folder_var.set(str(folder))
        saved = (load_settings().get("reject_dirs") or {}).get(str(folder.resolve())) if folder.is_dir() else None
        self.reject_var.set(saved or "")
        self.frames, self.selection = [], None
        self.overrides = read_overrides(folder / REPORT_NAME) if folder.is_dir() else {}
        self._previews.clear()
        self._preview_name = None
        self._clear_preview(tr("gui.preview.hint"))
        self._fill_table()
        self._set_summary_text("")
        self.progress.configure(value=0)
        if not folder.is_dir():
            self._status(lambda: tr("gui.status.no_folder", folder=folder))
        elif (folder / REPORT_NAME).is_file():
            self._load_report()
        else:
            files, home_of = collect_files(folder, self._reject_dir(), recursive)
            n = len(files)
            k = len({Path(home_of.get(str(p), p)).resolve().parent for p in files})
            if recursive and n:
                self._status(lambda: tr("gui.status.found_dirs", n=n, k=k))
            else:
                self._status(lambda: tr("gui.status.found", n=n) if n else tr("gui.status.no_fits"))
        self._update_result_view()
        self._update_buttons()

    def _recursive_changed(self) -> None:
        """使用者切換「包含子資料夾」：檔案範圍變了，照新的範圍重新開一次同一個資料夾。"""
        folder = self._folder()
        if self._setting_folder or folder is None:
            return
        if self.is_busy():  # 量測中不能換範圍，開關切回去
            self._setting_folder = True
            try:
                self.recursive_var.set(not self._recursive())
            finally:
                self._setting_folder = False
            return
        self._set_folder(folder, self._recursive())

    def _load_report(self) -> None:
        folder, reject_dir = self._folder(), self._reject_dir()
        if folder is None:
            return
        try:
            frames = read_csv(folder / REPORT_NAME, folder)
            files, home_of = collect_files(folder, reject_dir, self._recursive())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.err.load", error=exc))
            return
        # 已經不在資料夾（也不在淘汰片資料夾）的就不算進來，免得影響整批的統計；
        # 路徑對不上的（檔案被搬動過）用檔名認回，手動覆寫也換到新位置
        kept, renamed = match_frames(frames, files, home_of, folder)
        n_new, n_renamed = len(files) - len(kept), len(renamed)
        if renamed:
            self.overrides = {renamed.get(k, k): v for k, v in self.overrides.items()}
        self.frames = kept
        self._apply()
        if renamed:
            self._save_report()  # 報表改寫成新的位置
        self._status(lambda: tr("gui.status.loaded", n=len(kept))
                     + (tr("gui.status.loaded_renamed", n=n_renamed) if n_renamed else "")
                     + (tr("gui.status.loaded_new", n=n_new) if n_new else ""))
        self._update_buttons()

    # ------------------------------------------------------------------ 量測

    def _measure_or_stop(self) -> None:
        if self.is_busy():
            self._stop()
        else:
            self._start_measure()

    def _start_measure(self) -> None:
        folder, reject_dir = self._folder(), self._reject_dir()
        if folder is None or not folder.is_dir():
            messagebox.showwarning(APP_NAME, tr("gui.warn.pick_folder"))
            return
        files, home_of = collect_files(folder, reject_dir, self._recursive())
        if not files:
            messagebox.showwarning(APP_NAME, tr("gui.status.no_fits"))
            return
        n, k = len(files), len(home_of)
        self._status(lambda: tr("gui.status.measuring", n=n,
                                extra=tr("gui.status.includes_rejected", n=k) if k else ""))
        self._run_measure(files, home_of, "measured")

    def _workers(self) -> int:
        try:
            return max(1, int(float(self.workers_var.get())))
        except (tk.TclError, ValueError):
            return default_workers()

    def _run_measure(self, files: list[Path], home_of: dict[str, str], kind: str) -> None:
        """在背景量測，量完送出 (kind, 結果)：measured 是整批重量，watched 是監看到的新檔案。"""
        self.cancel.clear()
        self.progress.configure(value=0, maximum=len(files))
        self._started = time.monotonic()
        self.worker = threading.Thread(target=self._measure_worker,
                                       args=(files, home_of, self._workers(), kind), daemon=True)
        self.worker.start()
        self._update_buttons()

    def _measure_worker(self, files: list[Path], home_of: dict[str, str], workers: int, kind: str) -> None:
        try:
            frames = measure_files(files, workers, cancel=self.cancel, home_of=home_of,
                                   progress=lambda i, n, m: self.events.put(("progress", i, n, m)))
            self.events.put((kind, frames))
        except Cancelled:
            self.events.put(("cancelled",))
        except Exception:  # noqa: BLE001
            self.events.put(("error", traceback.format_exc()))

    def _stop(self) -> None:
        self.cancel.set()
        self.watch_var.set(False)  # 按停止就連監看一起停，免得下一輪又自動開始
        self._status(lambda: tr("gui.status.stopping"))

    # ------------------------------------------------------------------ 監看資料夾

    WATCH_INTERVAL_MS = 10_000

    def _toggle_watch(self) -> None:
        if self._watch_job is not None:
            self.after_cancel(self._watch_job)
            self._watch_job = None
        self._watch_sizes = {}
        if self.watch_var.get():
            n = len(self.frames)
            self._status(lambda: tr("gui.status.watching", n=n))
            self._watch_tick()
        elif not self.is_busy():
            self._status(lambda: tr("gui.status.watch_off"))

    def _watch_tick(self) -> None:
        """檢查一次資料夾。新檔案的大小要連續兩次一樣（相機軟體寫完了）才量測。"""
        if self._watch_job is not None:
            self.after_cancel(self._watch_job)
        self._watch_job = None
        if not self.watch_var.get():
            return
        self._watch_job = self.after(self.WATCH_INTERVAL_MS, self._watch_tick)
        folder, reject_dir = self._folder(), self._reject_dir()
        if self.is_busy() or folder is None or not folder.is_dir():
            return
        # 子資料夾模式下拍攝中新建的資料夾（例如新的一晚）也會被找到
        files, home_of = collect_files(folder, reject_dir, self._recursive())
        known = {frame_key(f.file, folder) for f in self.frames}
        sizes, ready = {}, []
        for p in files:
            if frame_key(home_of.get(str(p), p), folder) in known:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            sizes[str(p)] = size
            if size > 0 and self._watch_sizes.get(str(p)) == size:
                ready.append(p)
        self._watch_sizes = sizes
        if ready:
            n = len(ready)
            self._status(lambda: tr("gui.status.watch_measuring", n=n))
            self._run_measure(ready, home_of, "watched")

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self._poll_job = self.after(100, self._poll)

    @staticmethod
    def _progress_text(i: int, n: int, eta: float, name: str) -> str:
        if eta >= 90:
            left = tr("gui.status.left_min", m=eta / 60)
        elif i < n:
            left = tr("gui.status.left_sec", s=eta)
        else:
            left = ""
        return tr("gui.status.progress", i=i, n=n, left=left, name=name)

    def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "preview":
            _, name, preview = event
            self._previews[name] = preview
            while len(self._previews) > 6:
                self._previews.popitem(last=False)
            if name == self._preview_name:
                self._show_preview()
            return
        if kind == "preview_error":
            if event[1] == self._preview_name:
                self._clear_preview(tr("gui.preview.failed", error=event[2]))
            return
        if kind == "progress":
            _, i, n, m = event
            self.progress.configure(value=i)
            eta = (time.monotonic() - self._started) / i * (n - i)
            name = Path(m.file).name
            self._status(lambda: self._progress_text(i, n, eta, name))
        elif kind == "watched":
            self.worker = None
            new = event[1]
            folder = self._folder()
            keys = {frame_key(m.file, folder) for m in new}
            self.frames = [f for f in self.frames if frame_key(f.file, folder) not in keys] + new
            self._apply()
            self._save_report()
            latest = max(new, key=lambda m: (m.date_obs or "", m.file))
            n, name, total = len(new), Path(latest.file).name, len(self.frames)
            self._status(lambda: tr("gui.status.watch_added", n=n, name=name, total=total))
            self._update_buttons()
        elif kind == "measured":
            self.worker = None
            self.frames = event[1]
            self._apply()
            self._save_report()
            took = (time.monotonic() - self._started) / 60
            n, errors = len(self.frames), sum(1 for f in self.frames if f.error)
            self._status(lambda: tr("gui.status.done", n=n, m=took)
                         + (tr("gui.status.done_errors", n=errors) if errors else "")
                         + tr("gui.status.done_saved", report=REPORT_NAME))
            self._update_buttons()
        elif kind == "cancelled":
            self.worker = None
            self._status(lambda: tr("gui.status.cancelled"))
            self.progress.configure(value=0)
            self._update_buttons()
        elif kind == "error":
            self.worker = None
            self._status(lambda: tr("gui.status.failed"))
            self._update_buttons()
            messagebox.showerror(APP_NAME, tr("gui.err.measure", error=event[1][-1500:]))

    def _save_report(self) -> None:
        folder = self._folder()
        if folder is not None and self.selection is not None:
            try:
                write_csv(self.selection.decisions, folder / REPORT_NAME, root=folder)
            except OSError as exc:
                messagebox.showwarning(APP_NAME, tr("gui.warn.report_save", error=exc))

    # ------------------------------------------------------------------ 挑片

    def _schedule_apply(self) -> None:
        self._update_method_state()
        if self._apply_job is not None:
            self.after_cancel(self._apply_job)
        # 跟 APU Astro 一樣，停止操作約 0.3 秒後才更新結果
        self._apply_job = self.after(300, self._apply)

    def _config(self) -> ScoreConfig | None:
        """讀介面上的門檻設定；數字不合理時回傳 None。"""
        method = self.method_var.get()
        try:
            cfg = ScoreConfig(pass_pct=float(self.pass_var.get()) / 100)
            if method == "min_score":
                cfg.min_score = float(self.min_score_var.get())
                ok = 0 <= cfg.min_score <= 100
            elif method == "keep_best":
                cfg.keep_best = float(self.keep_best_var.get()) / 100
                ok = 0 < cfg.keep_best <= 1
            else:
                ok = 0 <= cfg.pass_pct <= 1
        except (tk.TclError, ValueError):
            return None
        return cfg if ok else None

    def _group_keys(self) -> tuple[str, ...]:
        keys = []
        if self.group_filter_var.get():
            keys.append("filter")
        if self.group_exposure_var.get():
            keys.append("exposure")
        if self.group_gain_var.get():
            keys.append("gain")
        if self.group_night_var.get():
            keys.append("night")
        if self.group_folder_var.get():
            keys.append("folder")
        return tuple(keys)

    def _apply(self) -> None:
        self._apply_job = None
        if not self.frames:
            return
        weights = self._weights()
        if weights is None:
            self._status(lambda: tr("gui.status.no_weights"))
            return
        cfg = self._config()
        if cfg is None:
            self._status(lambda: tr("gui.status.bad_threshold"))
            return
        total = sum(weights.values())
        cfg.weights = {m: w / total for m, w in weights.items()}
        save_settings(weights=weights)
        try:
            tolerance = max(0.0, float(self.exposure_tol_var.get()))
        except (tk.TclError, ValueError):
            tolerance = EXPOSURE_TOLERANCE
        save_settings(exposure_tolerance=tolerance)
        self.selection = sel = decide(self.frames, self._group_keys(), "score", cfg, exposure_tolerance=tolerance,
                                      overrides=self.overrides, root=self._folder())
        n, kept = len(sel.decisions), sel.n_keep
        th = sel.thresholds
        if len(th) == 1:
            extra = tr("gui.summary.one_threshold", t=next(iter(th.values())))
        else:
            extra = tr("gui.summary.groups", g=len(th)) if th else ""
        self.summary_var.set(tr("gui.summary", n=n, keep=kept, reject=n - kept, extra=extra))

        self.metrics["frames"].set(str(n))
        self.metrics["kept"].set(str(kept))
        self.metrics["rejected"].set(str(n - kept))
        self.metrics["manual"].set(str(sum(1 for d in sel.decisions if d.override)))
        results = [r for r in sel.results.values() if r is not None]
        if len(results) == 1:
            self.metrics["threshold"].set(f"{results[0].threshold:.1f}")
            ref_fwhm = results[0].reference.get("fwhm")
            self.metrics["ref_fwhm"].set(f"{ref_fwhm:.2f} px" if ref_fwhm is not None else "—")
        else:
            self.metrics["threshold"].set(tr("gui.metric.per_group") if results else "—")
            self.metrics["ref_fwhm"].set(tr("gui.metric.per_group") if results else "—")

        lines = self._show_exposure(sel)
        parts = [f"{tr_metric(m)} {cfg.weights[m] * 100:.0f}%" for m in METRICS]
        lines += [tr("summary.weights", parts=tr("summary.part_sep").join(parts)), ""]
        for label, size in sel.group_sizes.items():
            lines.append(tr("summary.group_header", label=label or tr("group.all"), n=size))
            result = sel.results.get(label)
            lines += score_summary_lines(result) if result else [tr("summary.no_measurable")]
            lines.append("")
        self._set_summary_text("\n".join(lines))
        self._plot_dirty = True
        self._draw_plot()
        self._fill_table()
        self._update_result_view()
        self._update_buttons()

    def _show_exposure(self, sel: Selection) -> list[str]:
        """保留片總曝光：右側「結果」一列（超過一組、一晚時每組、每晚各一行），回傳門檻細節分頁用的幾行字。"""
        total, n_kept, missing = kept_exposure(sel.decisions)
        self.metrics["kept_exposure"].set(format_duration(total) if total else "—")
        rows: list[tuple[str, float, int]] = []
        lines = [tr("summary.exposure", t=format_duration(total), n=n_kept)]
        if missing:
            lines.append(tr("summary.exposure_missing", n=missing))
        if len(sel.group_sizes) > 1:
            # 面板上只寫各組不一樣的那幾段（例如每晚分開時只寫日期），組名太長擠不下
            sep = tr("group.sep")
            common = set.intersection(*(set(label.split(sep)) for label in sel.group_sizes))
            for label in sel.group_sizes:
                t, n, _ = kept_exposure([d for d in sel.decisions if d.group == label])
                short = sep.join(p for p in label.split(sep) if p not in common)
                rows.append((short or label or tr("group.all"), t, n))
                lines.append(tr("summary.exposure_line", label=label or tr("group.all"), n=n, t=format_duration(t)))
        nights = assign_nights([d.metrics for d in sel.decisions])
        night_labels = sorted(set(nights.values()))
        if len(night_labels) > 1 and not self.group_night_var.get():  # 每晚分開時，上面每組就是每晚
            lines.append(tr("summary.exposure_nights"))
            for night in night_labels:
                t, n, _ = kept_exposure([d for d in sel.decisions if nights.get(d.metrics.file) == night])
                rows.append((night, t, n))
                lines.append(tr("summary.exposure_line", label=night, n=n, t=format_duration(t)))
        for child in self.exposure_rows.winfo_children():
            child.destroy()
        width = self.px(Darkroom.panel_width - 50)  # 面板內容的寬度（扣掉左右留白、捲軸）
        for label, t, n in rows:
            row = MetricRow(self.exposure_rows, self, "")
            value = tr("gui.metric.exposure_row", t=format_duration(t), n=n)
            row.set(value)
            # 組名太長（例如「無濾鏡／曝光 300s／增益 111／2026-09-05 晚」）就截短，數字才不會被擠出去
            room = width - self.fonts.mono.measure(value) - self.px(12)
            title = row.winfo_children()[0]
            title.configure(text=_fit(f"  {label}", self.fonts.small, room))
            Tooltip(title, label, self)
        self._tag_widgets(self.exposure_rows)
        lines.append("")
        return lines

    def _draw_plot(self) -> None:
        """趨勢圖要重畫時，等切到趨勢圖分頁才畫（在清單上連續挑片時不用每次都重畫）。"""
        if not self._plot_dirty or self.selection is None or self.nb.index("current") != 0:
            return
        self._plot_dirty = False
        sel = self.selection
        self._plot_order = draw_decisions(self.fig, sel.decisions, sel.thresholds, dark=True)
        folder = self._folder()
        names = [frame_key(d.metrics.file, folder) for d in self._plot_order]
        for ax in self.fig.axes:  # 滑鼠移到點上時，工具列顯示是哪一張
            ax.format_coord = lambda x, _y, names=names: self._plot_coord(names, x)
        self.canvas.draw_idle()

    @staticmethod
    def _plot_coord(names: list[str], x: float) -> str:
        i = int(round(x))
        return tr("gui.plot.coord", name=names[i]) if 0 <= i < len(names) else ""

    def _plot_clicked(self, event) -> None:
        """在趨勢圖上點一下：跳到清單裡的那一張（平移、放大模式時不算）。"""
        if event.button != 1 or event.inaxes is None or event.xdata is None or self.toolbar.mode:
            return
        i = int(round(event.xdata))
        if 0 <= i < len(self._plot_order):
            self.show_frame(frame_key(self._plot_order[i].metrics.file, self._folder()))

    def show_frame(self, key: str) -> None:
        """切到清單分頁，選取這一張並捲到中間；開著「只看淘汰片」而這張是保留的，先關掉才找得到。"""
        if not self.tree.exists(key) and self.only_reject_var.get():
            self.only_reject_var.set(False)
        if not self.tree.exists(key):
            return
        self.nb.select(1)
        self.tree.selection_set(key)
        self.tree.focus(key)
        self.tree.focus_set()
        items = self.tree.get_children()
        self.tree.update_idletasks()
        first, last = self.tree.yview()
        visible = (last - first) * len(items)
        self.tree.yview_moveto(max(0.0, (items.index(key) - visible / 2) / len(items)))

    def _set_summary_text(self, text: str) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")

    def _fill_table(self) -> None:
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        columns = [c[0] for c in COLUMNS]
        if not self._recursive():
            columns.remove("folder")
        self.tree.configure(displaycolumns=columns)
        if self.selection is None:
            return
        rows = sorted(self.selection.decisions, key=lambda d: (d.metrics.date_obs or "", d.metrics.file))
        keep_text, reject_text = tr("term.keep"), tr("term.reject")
        folder = self._folder()
        for d in rows:
            if self.only_reject_var.get() and d.keep:
                continue
            m = d.metrics
            # 子資料夾模式下單眼的檔名常常每晚重複，清單用相對路徑當代號
            key = frame_key(m.file, folder)
            sub = key.rpartition("/")[0]
            result = keep_text if d.keep else reject_text
            if d.override:
                result = f"✎ {result}"  # 手動覆寫
            self.tree.insert("", "end", iid=key, tags=() if d.keep else ("reject",), values=(
                Path(m.file).name, sub, result, _fmt(d.score, ".1f"),
                _fmt(m.fwhm, ".2f"), _fmt(m.eccentricity, ".2f"), m.n_stars, _fmt(m.background, ".0f"),
                _fmt(m.snr, ".1f"), _fmt(m.altitude, ".0f"), _fmt(m.moon_alt, ".0f"),
                d.group, "; ".join(str(r) for r in d.reasons)))
        if self._sort[0]:
            self._sort_by(self._sort[0], toggle=False)
        keep = [iid for iid in selected if self.tree.exists(iid)]
        if keep:
            self.tree.selection_set(keep)

    def _show_row_menu(self, event: tk.Event) -> str:
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        if self.tree.selection():
            self.row_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _set_override(self, choice: str | None) -> None:
        """把清單上選取的片強制保留 / 強制淘汰（choice=None 改回自動），並馬上存進報表。"""
        names = list(self.tree.selection())
        if not names or self.selection is None:
            return
        for name in names:
            if choice is None:
                self.overrides.pop(name, None)
            else:
                self.overrides[name] = choice
        self._apply()
        self._save_report()

    def _key_override(self, event: tk.Event, choice: str | None) -> str | None:
        """清單上按 K / X / A：跟按「強制保留／強制淘汰／改回自動」一樣；只選一張時接著選下一張。"""
        if event.state & MODIFIER_MASK:
            return None
        selected = self.tree.selection()
        if not selected:
            return "break"
        following = None
        if len(selected) == 1:
            items = self.tree.get_children()
            i = items.index(selected[0])
            following = items[i + 1] if i + 1 < len(items) else None
        self._set_override(choice)
        if following is not None and self.tree.exists(following):
            self.tree.selection_set(following)
            self.tree.focus(following)
            self.tree.see(following)
        return "break"

    def _sort_by(self, key: str, toggle: bool = True) -> None:
        """點欄位標題排序，同一欄再點一次反過來；toggle=False 用在重填清單後照原本的排序。"""
        last_key, last_reverse = self._sort
        if not toggle:
            reverse = last_reverse
        else:
            reverse = not last_reverse if key == last_key else False
        self._sort = (key, reverse)

        def value(item: str):
            v = self.tree.set(item, key)
            try:
                return (0, float(v))
            except ValueError:
                return (1 if v == "-" else 0, v)

        items = sorted(self.tree.get_children(), key=value, reverse=reverse)
        for i, item in enumerate(items):
            self.tree.move(item, "", i)

    # ------------------------------------------------------------------ 搬檔

    def _move(self) -> None:
        sel, reject_dir, folder = self.selection, self._reject_dir(), self._folder()
        if sel is None or folder is None:
            return
        recursive = self._recursive()
        out, back = move_files(sel.decisions, reject_dir, recursive, dry_run=True, folder=folder)
        if not out and not back:
            messagebox.showinfo(APP_NAME, tr("gui.move.nothing"))
            return
        lines = []
        if out and recursive:
            # 每個資料夾各搬幾張，列出來讓使用者確認範圍對不對（例如有沒有掃到不該掃的資料夾）
            counts: dict[str, int] = {}
            for target in out:
                # 自訂資料夾：照子資料夾結構放，相對於它；預設：各資料夾裡的 rejected，往上一層
                sub = frame_key(target.parent, reject_dir) if reject_dir else frame_key(target.parent.parent, folder)
                counts[sub] = counts.get(sub, 0) + 1
            shown = sorted(counts.items())[:12]
            head = tr("gui.move.out_mirror", n=len(out), dir=reject_dir) if reject_dir else tr("gui.move.out_each",
                                                                                              n=len(out))
            lines.append("\n".join([head]
                                   + [tr("gui.move.dir_line", dir=sub if sub != "." else tr("group.top_folder"), n=n)
                                      for sub, n in shown]
                                   + ([tr("gui.move.more_dirs", n=len(counts) - len(shown))]
                                      if len(counts) > len(shown) else [])))
        elif out:
            lines.append(tr("gui.move.out", n=len(out), dir=reject_dir))
        if back:
            lines.append(tr("gui.move.back", n=len(back)))
        if not messagebox.askyesno(APP_NAME, "\n\n".join(lines) + "\n\n" + tr("gui.move.confirm")):
            return
        try:
            out, back = move_files(sel.decisions, reject_dir, recursive, folder=folder)
            write_csv(sel.decisions, folder / REPORT_NAME, root=folder)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.move.error", error=exc))
            return
        n_out, n_back = len(out), len(back)

        def text() -> str:
            parts = []
            if n_out:
                parts.append(tr("gui.move.done_out", n=n_out))
            if n_back:
                parts.append(tr("gui.move.done_back", n=n_back))
            where = tr("gui.move.where_each") if reject_dir is None else tr("gui.move.where", dir=reject_dir)
            return tr("gui.move.sep").join(parts) + where

        self._status(text)

    def _restore(self) -> None:
        reject_dir, folder = self._reject_dir(), self._folder()
        if folder is None:
            return
        recursive = self._recursive()
        n = len(restore_files(folder, reject_dir, recursive, dry_run=True))
        if n == 0:
            messagebox.showinfo(APP_NAME, tr("gui.restore.nothing"))
            return
        question = (tr("gui.restore.confirm_each", n=n) if reject_dir is None
                    else tr("gui.restore.confirm", dir=reject_dir, n=n))
        if not messagebox.askyesno(APP_NAME, question):
            return
        try:
            restored = len(restore_files(folder, reject_dir, recursive))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.restore.error", error=exc))
            return
        self._status(lambda: tr("gui.restore.done", n=restored))

    # ------------------------------------------------------------------ 整理檔案

    def _organize_keys(self) -> tuple[str, ...]:
        return tuple(k for k in ORGANIZE_KEYS if self.organize_vars[k].get())

    def _moved_to_rejects(self, folder: Path) -> bool:
        """有淘汰片還在淘汰片資料夾裡：整理前後要先全部還原，不然搬移紀錄的原位置會對不上。"""
        return bool(collect_files(folder, self._reject_dir(), self._recursive())[1])

    def _organize(self) -> None:
        """依勾選的參數把影像搬進子資料夾（例如 ASI2600MC/Ha/300s/），報表、手動覆寫跟著換位置。"""
        folder = self._folder()
        if folder is None or not self.frames or self.is_busy():
            return
        keys = self._organize_keys()
        if not keys:
            messagebox.showinfo(APP_NAME, tr("gui.organize.no_keys"))
            return
        if self._moved_to_rejects(folder):
            messagebox.showinfo(APP_NAME, tr("gui.organize.restore_first"))
            return
        try:
            tolerance = max(0.0, float(self.exposure_tol_var.get()))
        except (tk.TclError, ValueError):
            tolerance = EXPOSURE_TOLERANCE
        plan = plan_organize(self.frames, folder, keys, tolerance)
        if not plan:
            messagebox.showinfo(APP_NAME, tr("gui.organize.nothing"))
            return
        counts: dict[str, int] = {}
        for _, target in plan:
            sub = frame_key(target.parent, folder)
            counts[sub] = counts.get(sub, 0) + 1
        shown = sorted(counts.items())[:12]
        dirs = "\n".join([tr("gui.move.dir_line", dir=sub, n=n) for sub, n in shown]
                         + ([tr("gui.move.more_dirs", n=len(counts) - len(shown))] if len(counts) > len(shown) else []))
        if not messagebox.askyesno(APP_NAME, tr("gui.organize.confirm", n=len(plan), dirs=dirs)):
            return
        self.watch_var.set(False)
        try:
            done = organize(plan, folder)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.organize.error", error=exc))
            self._set_folder(folder)  # 搬到一半：重開資料夾，報表用檔名認回已經搬走的
            return
        self._relocate_frames({cur: target for cur, target in done})
        n, k = len(done), len(counts)
        self._status(lambda: tr("gui.organize.done", n=n, k=k))

    def _undo_organize(self) -> None:
        folder = self._folder()
        if folder is None or self.is_busy():
            return
        n = pending_undo(folder)
        if n == 0:
            messagebox.showinfo(APP_NAME, tr("gui.organize.undo_nothing"))
            return
        if self._moved_to_rejects(folder):
            messagebox.showinfo(APP_NAME, tr("gui.organize.restore_first"))
            return
        if not messagebox.askyesno(APP_NAME, tr("gui.organize.undo_confirm", n=n)):
            return
        self.watch_var.set(False)
        try:
            done = undo_organize(folder)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.organize.error", error=exc))
            self._set_folder(folder)
            return
        self._relocate_frames({cur: orig for cur, orig in done})
        restored = len(done)
        self._status(lambda: tr("gui.organize.undo_done", n=restored))

    def _relocate_frames(self, moved: dict[Path, Path]) -> None:
        """檔案搬了位置（{原本的絕對路徑: 新位置}）：量測結果、手動覆寫換到新位置，存回報表；
        有影像在子資料夾裡就改成包含子資料夾。"""
        folder = self._folder()
        if folder is None:
            return
        new_keys: dict[str, str] = {}
        for f in self.frames:
            new = moved.get(Path(f.file).resolve())
            if new is not None:
                old = frame_key(f.file, folder)
                f.file = str(new)
                new_keys[old] = frame_key(new, folder)
        self.overrides = {new_keys.get(k, k): v for k, v in self.overrides.items()}
        self._previews.clear()
        self._preview_name = None
        self._clear_preview(tr("gui.preview.hint"))
        files, home_of = collect_files(folder, None, True)
        recursive = any("/" in frame_key(home_of.get(str(p), p), folder) for p in files)
        self._setting_folder = True
        try:
            self.recursive_var.set(recursive)
        finally:
            self._setting_folder = False
        if self.frames:
            self._apply()
            self._save_report()
        self._update_buttons()

    def close(self) -> None:
        """取消背景工作、停掉排程。呼叫端接著 destroy() 這個 View；整合版關掉分頁後請在主執行緒 gc.collect()，
        不然 View 的 Tk 變數可能在背景執行緒被 Python 的循環回收釋放（tkinter 會忽略，但不乾淨）。"""
        self.cancel.set()
        for job in (self._poll_job, self._apply_job, self._watch_job, self._preview_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._poll_job = self._apply_job = self._watch_job = self._preview_job = None
        self.close_popover()


# ---------------------------------------------------------------------- 視窗


def _build_menubar(root: tk.Tk, view: PickView) -> None:
    """Mac 的選單列，換掉 Tk 預設的英文選單；換語言時要重建一次。

    Mac 的快捷鍵慣例是放在選單項目上（⌘O 開啟資料夾）。
    「關於」顯示系統的關於視窗（版本、圖示取自 Info.plist）；隱藏、結束由系統提供。
    """
    menubar = tk.Menu(root)
    app_menu = tk.Menu(menubar, name="apple", tearoff=False)
    app_menu.add_command(label=tr("gui.menu.about"), command=lambda: root.tk.call("::tk::mac::standardAboutPanel"))
    app_menu.add_separator()
    menubar.add_cascade(menu=app_menu)
    file_menu = tk.Menu(menubar, tearoff=False)
    file_menu.add_command(label=tr("gui.menu.open"), accelerator="Command-O", command=view.ask_open)
    menubar.add_cascade(label=tr("gui.menu.file"), menu=file_menu)
    edit_menu = tk.Menu(menubar, tearoff=False)
    edit_menu.add_command(label=tr("gui.menu.copy"), accelerator="Command-C", command=view.copy)
    edit_menu.add_command(label=tr("gui.menu.select_all"), accelerator="Command-A", command=view.select_all)
    menubar.add_cascade(label=tr("gui.menu.edit"), menu=edit_menu)
    menubar.add_cascade(label=tr("gui.menu.window"), menu=tk.Menu(menubar, name="window"))
    root.configure(menu=menubar)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # 打包成沒有主控台的 exe 時 stdout / stderr 是 None，有人 print 就會當掉
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    lang = load_settings().get("language")
    if lang in LANGUAGES:
        set_language(lang)
    enable_dpi_awareness()
    root = tk.Tk()
    if IS_MAC:
        # Mac 的 Tk 以 72 dpi 計算，9 點的字只有 Windows（96 dpi）的四分之三大；調成一樣，兩邊的版面才一致
        root.tk.call("tk", "scaling", 96 / 72)
    if ICON.is_file():
        try:
            root.iconbitmap(default=str(ICON))
        except tk.TclError:
            pass
    scale = root.winfo_fpixels("1i") / 96.0
    # 螢幕放不下完整大小（例如 13 吋 MacBook Air 的 1470×956）就縮到螢幕裡
    width = min(int(1320 * scale), root.winfo_screenwidth() - int(40 * scale))
    height = min(int(900 * scale), root.winfo_screenheight() - int(110 * scale))
    root.geometry(f"{width}x{height}")
    root.minsize(int(1080 * scale), int(720 * scale))
    root.configure(bg=Darkroom.canvas)
    root.title(f"{APP_NAME} {__version__}")

    def change_language(lang: str) -> None:
        set_language(lang)
        save_settings(language=lang)
        view.rebuild()
        if IS_MAC:
            _build_menubar(root, view)  # 選單文字跟著換

    view = PickView(root, root, argv[0] if argv else None, on_language=change_language)
    view.pack(fill="both", expand=True)
    if IS_MAC:
        _build_menubar(root, view)
    else:  # Mac 的 ⌘O 在選單列
        root.bind_all("<Control-o>", lambda _e: view.ask_open())

    def on_close() -> None:
        if view.is_busy() and not messagebox.askyesno(APP_NAME, tr("gui.close.confirm")):
            return
        view.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    if IS_MAC:  # ⌘Q 跟按紅色關閉鈕一樣：量測中要先問；Tk 預設是直接結束程式
        root.createcommand("::tk::mac::Quit", on_close)
        root.createcommand("::tk::mac::OpenDocument", lambda *paths: paths and view.open_folder(paths[0]))
    dark_title_bar(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
