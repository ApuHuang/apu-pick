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
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from . import __version__
from .i18n import APP_NAME, APP_SUBTITLE, LANGUAGES, get_language, set_language, tr
from .metrics import FITS_SUFFIXES, FrameMetrics
from .mover import restore_rejected, sync_files
from .pipeline import (REJECT_DIR_NAME, REPORT_NAME, Cancelled, Selection, collect_files, decide,
                       measure_files)
from .plot import DARK, draw_decisions
from .report import read_csv, score_summary_lines, write_csv
from .scoring import ScoreConfig
from .settings import load_settings, save_settings

ASSETS = Path(__file__).parent / "assets"
ICON = ASSETS / "app.ico"
LOGO = ASSETS / "icon_128.png"


class Darkroom:
    """暗房介面的色票與尺寸，跟 APU Astro 的 DarkroomTheme 同一套。

    固定深色、不跟系統設定：星空影像的背景亮度、色偏跟周圍環境有關，淺色視窗會拉走對比的判斷。
    """
    canvas = "#000000"
    chrome = "#1c1c1c"
    panel = "#252525"
    group_header = "#2f2f2f"
    control = "#3d3d3d"
    separator = "#454545"
    label = "#ebebeb"
    secondary = "#949494"
    accent = "#5cadff"
    beta = "#ffa842"            # 頂部列的版本標籤
    prominent = "#0a84ff"       # 主要按鈕（macOS 深色模式的系統強調色）
    segment_on = "#636366"      # 分段切換選中的那格
    hover = "#4a4a4a"
    list_bg = "#141414"
    reject = DARK["reject"]
    top_bar_height = 48
    status_bar_height = 28
    panel_width = 320


# 清單欄位：(key, 標題的翻譯代號, 寬度 px, 對齊)
COLUMNS = [
    ("file", "gui.col.file", 330, "w"),
    ("result", "gui.col.result", 60, "center"),
    ("score", "gui.col.score", 55, "e"),
    ("fwhm", "gui.col.fwhm", 55, "e"),
    ("ecc", "gui.col.ecc", 80, "e"),
    ("stars", "gui.col.stars", 60, "e"),
    ("bkg", "gui.col.bkg", 80, "e"),
    ("group", "gui.col.group", 170, "w"),
    ("reason", "gui.col.reason", 360, "w"),
]


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _fmt(v: float | None, spec: str) -> str:
    return "-" if v is None or v != v else format(v, spec)


def _short_path(path: str, limit: int = 40) -> str:
    """太長的路徑中間用 … 省略，保留開頭跟最後的資料夾名稱。"""
    if len(path) <= limit:
        return path
    head = limit // 3
    return f"{path[:head]}…{path[-(limit - head - 1):]}"


def _trace(widget: tk.Misc, variable: tk.Variable, callback: Callable[[], None]) -> None:
    """變數改了就呼叫 callback；元件被刪掉時一起拿掉，切換語言重建介面時才不會呼叫到已刪除的元件。"""
    name = variable.trace_add("write", lambda *_: callback())

    def remove(event: tk.Event) -> None:
        if event.widget is widget:
            try:
                variable.trace_remove("write", name)
            except tk.TclError:
                pass

    widget.bind("<Destroy>", remove, add="+")


class Fonts:
    def __init__(self, root: tk.Tk):
        families = set(font.families(root))
        ui = next((f for f in ("Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC", "Helvetica Neue")
                   if f in families), "TkDefaultFont")
        mono = next((f for f in ("Consolas", "Menlo", "DejaVu Sans Mono") if f in families), "TkFixedFont")
        self.title = font.Font(root, family=ui, size=11, weight="bold")
        self.badge = font.Font(root, family=ui, size=7, weight="bold")
        self.ui = font.Font(root, family=ui, size=9)
        self.small = font.Font(root, family=ui, size=9)
        self.bold = font.Font(root, family=ui, size=9, weight="bold")
        self.mono = font.Font(root, family=mono, size=9)
        self.hero = font.Font(root, family=ui, size=24, weight="bold")
        self.hero_sub = font.Font(root, family=ui, size=11)
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            font.nametofont(name).configure(family=ui, size=9)


# ---------------------------------------------------------------------- 元件


class Tooltip:
    """滑鼠停一下才出現的說明，對應 APU Astro 按鈕的 .help。"""

    def __init__(self, widget: tk.Widget, text: str, app: App):
        self.widget, self.text, self.app = widget, text, app
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._job = self.widget.after(600, self._show)

    def _show(self) -> None:
        if not self.widget.winfo_exists():
            return
        D = Darkroom
        self._tip = tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        tip.configure(bg=D.separator)
        tk.Label(tip, text=self.text, font=self.app.fonts.small, fg=D.label, bg=D.group_header,
                 justify="left", wraplength=self.app.px(300), padx=self.app.px(8), pady=self.app.px(4)
                 ).pack(padx=1, pady=1)
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() - tip.winfo_reqwidth()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + self.app.px(4)
        tip.geometry(f"+{max(x, 0)}+{y}")

    def _hide(self, _event: object = None) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class Switch(tk.Canvas):
    """開關：對應 APU Astro 的 ParameterToggle。"""

    def __init__(self, master: tk.Widget, app: App, variable: tk.BooleanVar, bg: str):
        self.w, self.h = app.px(30), app.px(17)
        super().__init__(master, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.bind("<Button-1>", lambda _e: variable.set(not variable.get()))
        _trace(self, variable, self._draw)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        on, w, h = bool(self.var.get()), self.w, self.h
        fill = Darkroom.prominent if on else Darkroom.control
        self.create_oval(0, 0, h - 1, h - 1, fill=fill, outline=fill)
        self.create_oval(w - h, 0, w - 1, h - 1, fill=fill, outline=fill)
        self.create_rectangle(h / 2, 0, w - h / 2, h - 1, fill=fill, outline=fill)
        pad = max(2, round(h * 0.12))
        d = h - 2 * pad - 1
        x = w - pad - d - 1 if on else pad
        self.create_oval(x, pad, x + d, pad + d, fill="white", outline="white")


class Segmented(tk.Frame):
    """分段切換：對應 APU Astro 頂部列的「繁中｜EN」。"""

    def __init__(self, master: tk.Widget, app: App, options: list[tuple[str, str]], variable: tk.StringVar,
                 stretch: bool = False):
        super().__init__(master, bg=Darkroom.control, padx=2, pady=2)
        self.var = variable
        self.labels: dict[str, tk.Label] = {}
        for i, (value, text) in enumerate(options):
            lb = tk.Label(self, text=text, font=app.fonts.small, cursor="hand2",
                          padx=app.px(10), pady=app.px(1))
            if stretch:
                lb.grid(row=0, column=i, sticky="ew", padx=1)
                self.columnconfigure(i, weight=1, uniform="seg")
            else:
                lb.pack(side="left", padx=1)
            lb.bind("<Button-1>", lambda _e, v=value: variable.set(v))
            self.labels[value] = lb
        _trace(self, variable, self._draw)
        self._draw()

    def _draw(self) -> None:
        current = self.var.get()
        for value, lb in self.labels.items():
            if value == current:
                lb.configure(bg=Darkroom.segment_on, fg="white")
            else:
                lb.configure(bg=Darkroom.control, fg=Darkroom.label)


class PanelGroup(tk.Frame):
    """右側面板裡可收合的一組，對應 APU Astro 的 PanelGroup。收合狀態會記住。"""

    def __init__(self, master: tk.Widget, app: App, key: str, title: str, info: str | None = None):
        D = Darkroom
        super().__init__(master, bg=D.panel)
        self.app, self.key = app, key
        header = tk.Frame(self, bg=D.group_header, height=app.px(34), cursor="hand2")
        header.pack(fill="x")
        header.pack_propagate(False)
        self.chevron = tk.Label(header, font=app.fonts.small, fg=D.secondary, bg=D.group_header, width=2)
        self.chevron.pack(side="left", padx=(app.px(8), 0))
        title_label = tk.Label(header, text=title, font=app.fonts.bold, fg=D.label, bg=D.group_header)
        title_label.pack(side="left")
        if info:
            InfoButton(header, app, info).pack(side="right", padx=app.px(10))
        for w in (header, self.chevron, title_label):
            w.bind("<Button-1>", self.toggle)
        self.body = tk.Frame(self, bg=D.panel, padx=app.px(14), pady=app.px(10))
        self.sep = tk.Frame(self, bg=D.separator, height=1)
        self.sep.pack(fill="x", side="bottom")
        self.expanded = app.panel_state.get(key, True)
        self._layout()
        self.pack(fill="x")

    def _layout(self) -> None:
        self.chevron.configure(text="▾" if self.expanded else "▸")
        if self.expanded:
            self.body.pack(fill="x", before=self.sep)
        else:
            self.body.pack_forget()

    def toggle(self, _event: object = None) -> None:
        self.expanded = not self.expanded
        self._layout()
        self.app.panel_state[self.key] = self.expanded
        save_settings(panel=self.app.panel_state)


class InfoButton(tk.Label):
    """分組標題上的 ⓘ，點了才顯示說明，對應 APU Astro 的 InfoButton。"""

    def __init__(self, master: tk.Widget, app: App, text: str):
        super().__init__(master, text="ⓘ", font=app.fonts.ui, fg=Darkroom.secondary, bg=Darkroom.group_header,
                         cursor="hand2")
        self.app, self.text = app, text
        self.bind("<Button-1>", self._toggle)
        Tooltip(self, tr("gui.details"), app)

    def _toggle(self, _event: object = None) -> str:
        if self.app.popover_owner is self:
            self.app.close_popover()
        else:
            self.app.show_popover(self, self.text)
        return "break"  # 不要連帶收合分組


class ParameterSlider(tk.Frame):
    """滑桿列：標題、目前的值（等寬數字）、滑桿，對應 APU Astro 的 ParameterSlider。"""

    def __init__(self, master: tk.Widget, app: App, title: str, variable: tk.Variable, lo: float, hi: float,
                 fmt: Callable[[float], str], step: float = 1.0):
        D = Darkroom
        super().__init__(master, bg=D.panel)
        self.var, self.fmt, self.step = variable, fmt, step
        head = tk.Frame(self, bg=D.panel)
        head.pack(fill="x")
        tk.Label(head, text=title, font=app.fonts.small, fg=D.secondary, bg=D.panel).pack(side="left")
        self.value = tk.Label(head, font=app.fonts.mono, fg=D.label, bg=D.panel)
        self.value.pack(side="right")
        ttk.Scale(self, from_=lo, to=hi, variable=variable, style="Dark.Horizontal.TScale",
                  command=self._moved).pack(fill="x", pady=(app.px(3), 0))
        _trace(self, variable, self._refresh)
        self._refresh()

    def _moved(self, value: str) -> None:
        snapped = round(float(value) / self.step) * self.step
        if abs(snapped - float(value)) > 1e-9:
            self.var.set(snapped)

    def _refresh(self) -> None:
        try:
            self.value.configure(text=self.fmt(float(self.var.get())))
        except (tk.TclError, ValueError):
            self.value.configure(text="—")


class ParameterToggle(tk.Frame):
    """開關列：標題填滿寬度，開關在右邊。"""

    def __init__(self, master: tk.Widget, app: App, title: str, variable: tk.BooleanVar, bg: str = Darkroom.panel):
        super().__init__(master, bg=bg)
        label = tk.Label(self, text=title, font=app.fonts.ui, fg=Darkroom.label, bg=bg, cursor="hand2")
        label.pack(side="left")
        label.bind("<Button-1>", lambda _e: variable.set(not variable.get()))
        Switch(self, app, variable, bg).pack(side="right")


class MetricRow(tk.Frame):
    """唯讀的數值列，對應 APU Astro 的 MetricRow。"""

    def __init__(self, master: tk.Widget, app: App, title: str):
        D = Darkroom
        super().__init__(master, bg=D.panel)
        tk.Label(self, text=title, font=app.fonts.small, fg=D.secondary, bg=D.panel).pack(side="left")
        self.value = tk.Label(self, text="—", font=app.fonts.mono, fg=D.label, bg=D.panel)
        self.value.pack(side="right")
        self.pack(fill="x", pady=app.px(1))

    def set(self, text: str) -> None:
        self.value.configure(text=text)


# ---------------------------------------------------------------------- 主視窗


class App:
    def __init__(self, root: tk.Tk, folder: str | None = None):
        self.root = root
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
        self.workers_var = tk.IntVar(value=default_workers())
        self.only_reject_var = tk.BooleanVar(value=False)
        self.folder_var = tk.StringVar()
        self.reject_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.summary_var = tk.StringVar()
        self.panel_state: dict[str, bool] = dict(load_settings().get("panel", {}))

        self._scale = root.winfo_fpixels("1i") / 96.0
        self.fonts = Fonts(root)
        root.title(f"{APP_NAME} {__version__}")
        root.geometry(f"{self.px(1320)}x{self.px(900)}")
        root.minsize(self.px(1080), self.px(720))
        root.configure(bg=Darkroom.canvas)
        self._setup_style()
        self._build()
        for var in (self.method_var, self.pass_var, self.min_score_var, self.keep_best_var,
                    self.group_filter_var, self.group_exposure_var, self.group_night_var):
            var.trace_add("write", lambda *_: self._schedule_apply())
        self.only_reject_var.trace_add("write", lambda *_: self._fill_table())
        # 延到事件處理完再換：換語言會重建介面，包含正在處理點擊的那個切換鈕
        self.lang_var.trace_add("write", lambda *_: root.after_idle(self._change_language))
        root.bind_all("<Control-o>", lambda _e: self._browse_folder())
        root.bind_all("<Button-1>", self._maybe_close_popover, add="+")
        root.bind_all("<Escape>", lambda _e: self.close_popover())
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_all()
        root.after(100, self._poll)
        if folder:
            self._set_folder(Path(folder))

    def px(self, v: float) -> int:
        return int(round(v * self._scale))

    # ------------------------------------------------------------------ 樣式與版面

    def _setup_style(self) -> None:
        D = Darkroom
        style = ttk.Style(self.root)
        style.theme_use("clam")
        flat = {"bordercolor": D.separator, "focuscolor": D.control}
        style.configure("Dark.TButton", background=D.control, foreground=D.label, lightcolor=D.control,
                        darkcolor=D.control, padding=(self.px(10), self.px(2)), font=self.fonts.ui, **flat)
        style.map("Dark.TButton",
                  background=[("disabled", D.group_header), ("pressed", D.segment_on), ("active", D.hover)],
                  lightcolor=[("active", D.hover)], darkcolor=[("active", D.hover)],
                  foreground=[("disabled", "#6b6b6b")])
        style.configure("Prominent.TButton", background=D.prominent, foreground="white", lightcolor=D.prominent,
                        darkcolor=D.prominent, bordercolor=D.prominent, focuscolor=D.prominent,
                        padding=(self.px(10), self.px(2)), font=self.fonts.ui)
        style.map("Prominent.TButton",
                  background=[("disabled", "#1d3550"), ("pressed", "#0064d2"), ("active", "#2a95ff")],
                  lightcolor=[("disabled", "#1d3550"), ("active", "#2a95ff")],
                  darkcolor=[("disabled", "#1d3550"), ("active", "#2a95ff")],
                  bordercolor=[("disabled", "#1d3550")],
                  foreground=[("disabled", "#7f93a8")])
        # 平的灰色軌道 + 淺色滑塊。background 同時是滑塊跟軌道外圍的顏色，所以拿掉外圍那層（focus / padding）
        style.layout("Dark.Horizontal.TScale", [("Horizontal.Scale.trough", {"sticky": "nswe", "children": [
            ("Horizontal.Scale.slider", {"side": "left", "sticky": ""})]})])
        style.configure("Dark.Horizontal.TScale", background=D.label, troughcolor=D.control, bordercolor=D.control,
                        lightcolor=D.control, darkcolor=D.control, gripcount=0)
        style.map("Dark.Horizontal.TScale", background=[("active", "white")])
        style.configure("Dark.Horizontal.TProgressbar", background=D.accent, troughcolor=D.control,
                        bordercolor=D.chrome, lightcolor=D.accent, darkcolor=D.accent)
        style.configure("Dark.TNotebook", background=D.canvas, borderwidth=0, tabmargins=(0, 0, 0, 0),
                        bordercolor=D.canvas, lightcolor=D.canvas, darkcolor=D.canvas)
        style.configure("Dark.TNotebook.Tab", background=D.chrome, foreground=D.secondary, bordercolor=D.separator,
                        lightcolor=D.chrome, darkcolor=D.chrome, padding=(self.px(14), self.px(4)),
                        font=self.fonts.ui)
        style.map("Dark.TNotebook.Tab", background=[("selected", D.group_header)],
                  foreground=[("selected", D.label)], lightcolor=[("selected", D.group_header)])
        style.configure("Canvas.TFrame", background=D.canvas)
        style.configure("Dark.Treeview", background=D.list_bg, fieldbackground=D.list_bg, foreground=D.label,
                        bordercolor=D.chrome, lightcolor=D.list_bg, darkcolor=D.list_bg,
                        rowheight=self.px(22), font=self.fonts.ui)
        style.map("Dark.Treeview", background=[("selected", "#1f3d63")], foreground=[("selected", D.label)])
        style.configure("Dark.Treeview.Heading", background=D.group_header, foreground=D.label,
                        bordercolor=D.separator, lightcolor=D.group_header, darkcolor=D.group_header,
                        relief="flat", font=self.fonts.bold)
        style.map("Dark.Treeview.Heading", background=[("active", D.control)])
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"Dark.{orient}.TScrollbar", background=D.control, troughcolor=D.chrome,
                            bordercolor=D.chrome, arrowcolor=D.secondary, lightcolor=D.control, darkcolor=D.control)
            style.map(f"Dark.{orient}.TScrollbar", background=[("active", D.hover)])
        # matplotlib 工具列的按鈕是一般 tk 元件：預設底色設成深色，它就會自己換成淺色圖示
        for pattern, value in (("*Button.background", D.chrome), ("*Button.foreground", D.label),
                               ("*Button.activeBackground", D.hover), ("*Button.highlightBackground", D.chrome),
                               ("*Checkbutton.background", D.chrome), ("*Checkbutton.foreground", D.label),
                               ("*Checkbutton.activeBackground", D.hover), ("*Checkbutton.selectColor", D.segment_on),
                               ("*Checkbutton.highlightBackground", D.chrome)):
            self.root.option_add(pattern, value)

    def _build(self) -> None:
        self._build_top_bar()
        self._build_status_bar()
        body = tk.Frame(self.root, bg=Darkroom.canvas)
        body.pack(fill="both", expand=True)
        self._build_panel(body)
        tk.Frame(body, bg=Darkroom.separator, width=1).pack(side="right", fill="y")
        self._build_canvas_area(body)

    def _build_top_bar(self) -> None:
        D = Darkroom
        bar = tk.Frame(self.root, bg=D.chrome, height=self.px(D.top_bar_height))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=D.separator, height=1).pack(fill="x")

        identity = tk.Frame(bar, bg=D.chrome)
        identity.pack(side="left", padx=(self.px(14), 0))
        tk.Label(identity, text=APP_NAME, font=self.fonts.title, fg=D.label, bg=D.chrome).pack(side="left")
        badge = tk.Frame(identity, bg=D.beta, padx=1, pady=1)  # 1px 橘色外框
        badge.pack(side="left", padx=(self.px(7), 0))
        tk.Label(badge, text=f"v{__version__}", font=self.fonts.badge, fg=D.beta, bg=D.chrome,
                 padx=self.px(4)).pack()

        actions = tk.Frame(bar, bg=D.chrome)
        actions.pack(side="right", padx=(0, self.px(14)))
        Segmented(actions, self, [("zh", "繁中"), ("en", "EN")], self.lang_var).pack(side="left")
        tk.Frame(actions, bg=D.separator, width=1, height=self.px(18)).pack(side="left", padx=self.px(10))
        self.open_btn = ttk.Button(actions, text=tr("gui.btn.open"), style="Dark.TButton",
                                   command=self._browse_folder)
        self.open_btn.pack(side="left")
        Tooltip(self.open_btn, tr("gui.btn.open.help"), self)
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
        bar = tk.Frame(self.root, bg=D.chrome, height=self.px(D.status_bar_height))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=D.separator, height=1).pack(side="bottom", fill="x")
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
        panel = tk.Frame(body, bg=D.panel, width=self.px(D.panel_width))
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)

        group = PanelGroup(panel, self, "result", tr("gui.group.result"), tr("gui.group.result.info"))
        self.metrics = {key: MetricRow(group.body, self, tr(f"gui.metric.{key}"))
                        for key in ("frames", "kept", "rejected", "threshold", "ref_fwhm")}

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

        group = PanelGroup(panel, self, "grouping", tr("gui.group.grouping"), tr("gui.group.grouping.info"))
        for key, var in (("filter", self.group_filter_var), ("exposure", self.group_exposure_var),
                         ("night", self.group_night_var)):
            ParameterToggle(group.body, self, tr(f"gui.toggle.{key}"), var).pack(fill="x", pady=self.px(2))

        group = PanelGroup(panel, self, "measure", tr("gui.group.measure"), tr("gui.group.measure.info"))
        ParameterSlider(group.body, self, tr("gui.slider.workers"), self.workers_var, 1,
                        max(2, os.cpu_count() or 2), lambda v: f"{v:.0f}").pack(fill="x")
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
        self.restore_btn = ttk.Button(row, text=tr("gui.btn.restore"), style="Dark.TButton", command=self._restore)
        self.restore_btn.pack(side="left", padx=(self.px(6), 0))

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
        toolbar = NavigationToolbar2Tk(self.canvas, plot_tab, pack_toolbar=False)
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
        self.canvas.get_tk_widget().configure(bg=D.canvas, highlightthickness=0)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        list_tab = ttk.Frame(self.nb, style="Canvas.TFrame")
        self.nb.add(list_tab, text=tr("gui.tab.list"))
        top = tk.Frame(list_tab, bg=D.canvas)
        top.pack(fill="x", padx=self.px(8), pady=self.px(6))
        ParameterToggle(top, self, tr("gui.only_reject"), self.only_reject_var, bg=D.canvas).pack(side="left")
        table = tk.Frame(list_tab, bg=D.canvas)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in COLUMNS], show="headings", style="Dark.Treeview")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=tr(title), command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=self.px(width), anchor=anchor, stretch=key in ("file", "reason"))
        self.tree.tag_configure("reject", foreground=D.reject)
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

    def _change_language(self) -> None:
        lang = self.lang_var.get()
        if lang not in LANGUAGES or lang == get_language():
            return
        if self._busy():  # 量測中不換：重建介面會打斷進度顯示
            self.lang_var.set(get_language())
            return
        set_language(lang)
        save_settings(language=lang)
        self._rebuild()

    def _rebuild(self) -> None:
        """換語言：整個重建介面，已經量好的結果、設定都保留。"""
        self.close_popover()
        for child in self.root.winfo_children():
            child.destroy()
        self._build()
        if self.frames:
            self._apply()
        self._refresh_all()

    # ------------------------------------------------------------------ 狀態

    def _status(self, fn: Callable[[], str]) -> None:
        self._status_fn = fn
        self.status_var.set(fn())

    def _folder(self) -> Path | None:
        text = self.folder_var.get().strip().strip('"')
        return Path(text) if text else None

    def _reject_dir(self) -> Path | None:
        folder = self._folder()
        text = self.reject_var.get().strip().strip('"')
        if text:
            return Path(text)
        return folder / REJECT_DIR_NAME if folder else None

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _refresh_all(self) -> None:
        """依目前狀態更新所有跟語言、資料有關的顯示。"""
        self.status_var.set(self._status_fn())
        self._update_buttons()
        self._update_method_state()
        self._update_result_view()

    def _update_buttons(self) -> None:
        busy = self._busy()
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

        D = Darkroom
        self.folder_title.configure(text=folder.name if folder else tr("gui.no_folder"),
                                    fg=D.label if folder else D.secondary)
        reject_dir = self._reject_dir()
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
            self.summary_var.set("")

    # ------------------------------------------------------------------ 資料夾

    def _browse_folder(self) -> None:
        if self._busy():
            return
        initial = self._folder()
        path = filedialog.askdirectory(title=tr("gui.pick_folder"),
                                       initialdir=str(initial) if initial and initial.is_dir() else None)
        if path:
            self._set_folder(Path(path))

    def _browse_reject(self) -> None:
        path = filedialog.askdirectory(title=tr("gui.pick_reject"))
        if path:
            self.reject_var.set(str(Path(path)))
            self._update_buttons()

    def _set_folder(self, folder: Path) -> None:
        if folder.is_file() and folder.suffix.lower() in FITS_SUFFIXES:
            folder = folder.parent  # 拖一張 FITS 進來也行
        self.folder_var.set(str(folder))
        self.reject_var.set(str(folder / REJECT_DIR_NAME))
        self.frames, self.selection = [], None
        self._fill_table()
        self._set_summary_text("")
        self.progress.configure(value=0)
        if not folder.is_dir():
            self._status(lambda: tr("gui.status.no_folder", folder=folder))
        elif (folder / REPORT_NAME).is_file():
            self._load_report()
        else:
            n = len(collect_files(folder, self._reject_dir())[0])
            self._status(lambda: tr("gui.status.found", n=n) if n else tr("gui.status.no_fits"))
        self._update_result_view()
        self._update_buttons()

    def _load_report(self) -> None:
        folder, reject_dir = self._folder(), self._reject_dir()
        if folder is None or reject_dir is None:
            return
        try:
            frames = read_csv(folder / REPORT_NAME, folder)
            files, home_of = collect_files(folder, reject_dir)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.err.load", error=exc))
            return
        now = {Path(home_of.get(str(p), p)).name for p in files}
        # 已經不在資料夾（也不在淘汰片資料夾）的就不算進來，免得影響整批的統計
        kept = [f for f in frames if Path(f.file).name in now]
        n_new = len(now - {Path(f.file).name for f in frames})
        self.frames = kept
        self._apply()
        self._status(lambda: tr("gui.status.loaded", n=len(kept))
                     + (tr("gui.status.loaded_new", n=n_new) if n_new else ""))
        self._update_buttons()

    # ------------------------------------------------------------------ 量測

    def _measure_or_stop(self) -> None:
        if self._busy():
            self._stop()
        else:
            self._start_measure()

    def _start_measure(self) -> None:
        folder, reject_dir = self._folder(), self._reject_dir()
        if folder is None or reject_dir is None or not folder.is_dir():
            messagebox.showwarning(APP_NAME, tr("gui.warn.pick_folder"))
            return
        try:
            workers = max(1, int(float(self.workers_var.get())))
        except (tk.TclError, ValueError):
            workers = default_workers()
        files, home_of = collect_files(folder, reject_dir)
        if not files:
            messagebox.showwarning(APP_NAME, tr("gui.status.no_fits"))
            return
        self.cancel.clear()
        self.progress.configure(value=0, maximum=len(files))
        n, k = len(files), len(home_of)
        self._status(lambda: tr("gui.status.measuring", n=n,
                                extra=tr("gui.status.includes_rejected", n=k) if k else ""))
        self._started = time.monotonic()
        self.worker = threading.Thread(target=self._measure_worker, args=(files, home_of, workers), daemon=True)
        self.worker.start()
        self._update_buttons()

    def _measure_worker(self, files: list[Path], home_of: dict[str, str], workers: int) -> None:
        try:
            frames = measure_files(files, workers, cancel=self.cancel, home_of=home_of,
                                   progress=lambda i, n, m: self.events.put(("progress", i, n, m)))
            self.events.put(("measured", frames))
        except Cancelled:
            self.events.put(("cancelled",))
        except Exception:  # noqa: BLE001
            self.events.put(("error", traceback.format_exc()))

    def _stop(self) -> None:
        self.cancel.set()
        self._status(lambda: tr("gui.status.stopping"))

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

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
        if kind == "progress":
            _, i, n, m = event
            self.progress.configure(value=i)
            eta = (time.monotonic() - self._started) / i * (n - i)
            name = Path(m.file).name
            self._status(lambda: self._progress_text(i, n, eta, name))
        elif kind == "measured":
            self.worker = None
            self.frames = event[1]
            self._apply()
            folder = self._folder()
            if folder is not None and self.selection is not None:
                try:
                    write_csv(self.selection.decisions, folder / REPORT_NAME)
                except OSError as exc:
                    messagebox.showwarning(APP_NAME, tr("gui.warn.report_save", error=exc))
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

    # ------------------------------------------------------------------ 挑片

    def _schedule_apply(self) -> None:
        self._update_method_state()
        if self._apply_job is not None:
            self.root.after_cancel(self._apply_job)
        # 跟 APU Astro 一樣，停止操作約 0.3 秒後才更新結果
        self._apply_job = self.root.after(300, self._apply)

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
        if self.group_night_var.get():
            keys.append("night")
        return tuple(keys)

    def _apply(self) -> None:
        self._apply_job = None
        if not self.frames:
            return
        cfg = self._config()
        if cfg is None:
            self._status(lambda: tr("gui.status.bad_threshold"))
            return
        self.selection = sel = decide(self.frames, self._group_keys(), "score", cfg)
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
        results = [r for r in sel.results.values() if r is not None]
        if len(results) == 1:
            self.metrics["threshold"].set(f"{results[0].threshold:.1f}")
            ref_fwhm = results[0].reference.get("fwhm")
            self.metrics["ref_fwhm"].set(f"{ref_fwhm:.2f} px" if ref_fwhm is not None else "—")
        else:
            self.metrics["threshold"].set(tr("gui.metric.per_group") if results else "—")
            self.metrics["ref_fwhm"].set(tr("gui.metric.per_group") if results else "—")

        lines: list[str] = []
        for label, size in sel.group_sizes.items():
            lines.append(tr("summary.group_header", label=label or tr("group.all"), n=size))
            result = sel.results.get(label)
            lines += score_summary_lines(result) if result else [tr("summary.no_measurable")]
            lines.append("")
        self._set_summary_text("\n".join(lines))
        draw_decisions(self.fig, sel.decisions, th, dark=True)
        self.canvas.draw_idle()
        self._fill_table()
        self._update_result_view()
        self._update_buttons()

    def _set_summary_text(self, text: str) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")

    def _fill_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.selection is None:
            return
        rows = sorted(self.selection.decisions, key=lambda d: (d.metrics.date_obs or "", d.metrics.file))
        keep_text, reject_text = tr("term.keep"), tr("term.reject")
        for d in rows:
            if self.only_reject_var.get() and d.keep:
                continue
            m = d.metrics
            self.tree.insert("", "end", tags=() if d.keep else ("reject",), values=(
                Path(m.file).name, keep_text if d.keep else reject_text, _fmt(d.score, ".1f"),
                _fmt(m.fwhm, ".2f"), _fmt(m.eccentricity, ".2f"), m.n_stars, _fmt(m.background, ".0f"),
                d.group, "; ".join(str(r) for r in d.reasons)))
        if self._sort[0]:
            self._sort_by(self._sort[0], toggle=False)

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
        if sel is None or reject_dir is None or folder is None:
            return
        out, back = sync_files(sel.decisions, reject_dir, dry_run=True)
        if not out and not back:
            messagebox.showinfo(APP_NAME, tr("gui.move.nothing"))
            return
        lines = []
        if out:
            lines.append(tr("gui.move.out", n=len(out), dir=reject_dir))
        if back:
            lines.append(tr("gui.move.back", n=len(back)))
        if not messagebox.askyesno(APP_NAME, "\n\n".join(lines) + "\n\n" + tr("gui.move.confirm")):
            return
        try:
            out, back = sync_files(sel.decisions, reject_dir)
            write_csv(sel.decisions, folder / REPORT_NAME)
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
            return tr("gui.move.sep").join(parts) + tr("gui.move.where", dir=reject_dir)

        self._status(text)

    def _restore(self) -> None:
        reject_dir, folder = self._reject_dir(), self._folder()
        if reject_dir is None or folder is None:
            return
        n = len(restore_rejected(reject_dir, folder, dry_run=True))
        if n == 0:
            messagebox.showinfo(APP_NAME, tr("gui.restore.nothing"))
            return
        if not messagebox.askyesno(APP_NAME, tr("gui.restore.confirm", dir=reject_dir, n=n)):
            return
        try:
            restored = len(restore_rejected(reject_dir, folder))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("gui.restore.error", error=exc))
            return
        self._status(lambda: tr("gui.restore.done", n=restored))

    def _on_close(self) -> None:
        if self._busy():
            if not messagebox.askyesno(APP_NAME, tr("gui.close.confirm")):
                return
            self.cancel.set()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    """高解析度螢幕上字才不會糊。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def _dark_title_bar(root: tk.Tk) -> None:
    """系統標題列也用深色（Windows 10 20H1 以後 / macOS），整個視窗才是一致的暗房。"""
    if sys.platform == "win32":
        try:
            import ctypes
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            value = ctypes.c_int(1)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（新版 20，舊版 19）
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                              ctypes.sizeof(value)) == 0:
                    break
        except (AttributeError, OSError):
            pass
    elif sys.platform == "darwin":
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "appearance", root, "darkaqua")
        except tk.TclError:
            pass


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
    _enable_dpi_awareness()
    root = tk.Tk()
    if ICON.is_file():
        try:
            root.iconbitmap(default=str(ICON))
        except tk.TclError:
            pass
    App(root, argv[0] if argv else None)
    _dark_title_bar(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
