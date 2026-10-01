"""暗房介面元件：APU Astro 系列共用的同一套元件，類別名稱與參數都不要改，將來才能直接抽成共用套件。

- setup_style(root, px, fonts) 設定 ttk 樣式
- 元件的 app 參數是 PickView（提供 px、fonts、panel_state、show_popover、close_popover、popover_owner）
"""

from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from tkinter import font, ttk

from .i18n import tr
from .settings import save_settings

IS_MAC = sys.platform == "darwin"


class Darkroom:
    """暗房介面的色票與尺寸（APU Astro 系列同一套）。

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
    beta = "#ffa842"
    prominent = "#0a84ff"
    segment_on = "#636366"
    hover = "#4a4a4a"
    list_bg = "#141414"
    reject = "#ff6961"
    top_bar_height = 48
    status_bar_height = 28
    panel_width = 320




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


def _short_path(path: str, limit: int = 40) -> str:
    if len(path) <= limit:
        return path
    head = limit // 3
    return f"{path[:head]}…{path[-(limit - head - 1):]}"


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

    def __init__(self, widget: tk.Widget, text: str, app: "PickView"):
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
    """開關：對應 APU Astro 的 ParameterToggle。停用時畫成暗色、點了沒反應。"""

    def __init__(self, master: tk.Widget, app: "PickView", variable: tk.BooleanVar, bg: str):
        self.w, self.h = app.px(30), app.px(17)
        super().__init__(master, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.enabled = True
        self.forced_off = False
        self.bind("<Button-1>", lambda _e: self.enabled and variable.set(not variable.get()))
        _trace(self, variable, self._draw)
        self._draw()

    def set_enabled(self, enabled: bool, forced_off: bool = False) -> None:
        """forced_off：功能用不了（例如沒有 GPU）時畫成關，但保留使用者原本的設定。"""
        self.enabled = enabled
        self.forced_off = forced_off and not enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        on, w, h = bool(self.var.get()) and not self.forced_off, self.w, self.h
        fill = (Darkroom.prominent if on else Darkroom.control) if self.enabled else (
            "#1d3550" if on else Darkroom.group_header)
        self.create_oval(0, 0, h - 1, h - 1, fill=fill, outline=fill)
        self.create_oval(w - h, 0, w - 1, h - 1, fill=fill, outline=fill)
        self.create_rectangle(h / 2, 0, w - h / 2, h - 1, fill=fill, outline=fill)
        pad = max(2, round(h * 0.12))
        d = h - 2 * pad - 1
        x = w - pad - d - 1 if on else pad
        knob = "white" if self.enabled else "#8a8a8a"
        self.create_oval(x, pad, x + d, pad + d, fill=knob, outline=knob)


class Segmented(tk.Frame):
    """分段切換：對應 APU Astro 頂部列的「繁中｜EN」。"""

    def __init__(self, master: tk.Widget, app: "PickView", options: list[tuple[str, str]], variable: tk.StringVar,
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

    def __init__(self, master: tk.Widget, app: "PickView", key: str, title: str, info: str | None = None):
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

    def __init__(self, master: tk.Widget, app: "PickView", text: str):
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

    def __init__(self, master: tk.Widget, app: "PickView", title: str, variable: tk.Variable, lo: float, hi: float,
                 fmt: Callable[[float], str], step: float = 1.0):
        D = Darkroom
        super().__init__(master, bg=D.panel)
        self.var, self.fmt, self.step = variable, fmt, step
        head = tk.Frame(self, bg=D.panel)
        head.pack(fill="x")
        self.title = tk.Label(head, text=title, font=app.fonts.small, fg=D.secondary, bg=D.panel)
        self.title.pack(side="left")
        self.value = tk.Label(head, font=app.fonts.mono, fg=D.label, bg=D.panel)
        self.value.pack(side="right")
        self.scale = ttk.Scale(self, from_=lo, to=hi, variable=variable, style="Dark.Horizontal.TScale",
                               command=self._moved)
        self.scale.pack(fill="x", pady=(app.px(3), 0))
        _trace(self, variable, self._refresh)
        self._refresh()

    def set_enabled(self, enabled: bool) -> None:
        self.scale.state(["!disabled"] if enabled else ["disabled"])
        self.value.configure(fg=Darkroom.label if enabled else "#6b6b6b")

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

    def __init__(self, master: tk.Widget, app: "PickView", title: str, variable: tk.BooleanVar, bg: str = Darkroom.panel):
        super().__init__(master, bg=bg)
        self.label = tk.Label(self, text=title, font=app.fonts.ui, fg=Darkroom.label, bg=bg, cursor="hand2")
        self.label.pack(side="left")
        self.switch = Switch(self, app, variable, bg)
        self.switch.pack(side="right")
        self.label.bind("<Button-1>", lambda _e: self.switch.enabled and variable.set(not variable.get()))

    def set_enabled(self, enabled: bool, forced_off: bool = False) -> None:
        self.switch.set_enabled(enabled, forced_off)
        self.label.configure(fg=Darkroom.label if enabled else "#6b6b6b")


class MetricRow(tk.Frame):
    """唯讀的數值列，對應 APU Astro 的 MetricRow。"""

    def __init__(self, master: tk.Widget, app: "PickView", title: str):
        D = Darkroom
        super().__init__(master, bg=D.panel)
        tk.Label(self, text=title, font=app.fonts.small, fg=D.secondary, bg=D.panel).pack(side="left")
        self.value = tk.Label(self, text="—", font=app.fonts.mono, fg=D.label, bg=D.panel)
        self.value.pack(side="right")
        self.pack(fill="x", pady=app.px(1))

    def set(self, text: str) -> None:
        self.value.configure(text=text)



def setup_style(root: tk.Misc, px: Callable[[float], int], fonts: Fonts) -> None:
    """ttk 樣式。"""
    D = Darkroom
    style = ttk.Style(root)
    style.theme_use("clam")
    flat = {"bordercolor": D.separator, "focuscolor": D.control}
    style.configure("Dark.TButton", background=D.control, foreground=D.label, lightcolor=D.control,
                    darkcolor=D.control, padding=(px(10), px(2)), font=fonts.ui, **flat)
    style.map("Dark.TButton",
              background=[("disabled", D.group_header), ("pressed", D.segment_on), ("active", D.hover)],
              lightcolor=[("active", D.hover)], darkcolor=[("active", D.hover)],
              foreground=[("disabled", "#6b6b6b")])
    style.configure("Prominent.TButton", background=D.prominent, foreground="white", lightcolor=D.prominent,
                    darkcolor=D.prominent, bordercolor=D.prominent, focuscolor=D.prominent,
                    padding=(px(10), px(2)), font=fonts.ui)
    style.map("Prominent.TButton",
              background=[("disabled", "#1d3550"), ("pressed", "#0064d2"), ("active", "#2a95ff")],
              lightcolor=[("disabled", "#1d3550"), ("active", "#2a95ff")],
              darkcolor=[("disabled", "#1d3550"), ("active", "#2a95ff")],
              bordercolor=[("disabled", "#1d3550")],
              foreground=[("disabled", "#7f93a8")])
    style.layout("Dark.Horizontal.TScale", [("Horizontal.Scale.trough", {"sticky": "nswe", "children": [
        ("Horizontal.Scale.slider", {"side": "left", "sticky": ""})]})])
    style.configure("Dark.Horizontal.TScale", background=D.label, troughcolor=D.control, bordercolor=D.control,
                    lightcolor=D.control, darkcolor=D.control, gripcount=0)
    style.map("Dark.Horizontal.TScale", background=[("disabled", "#6b6b6b"), ("active", "white")])
    style.configure("Dark.Horizontal.TProgressbar", background=D.accent, troughcolor=D.control,
                    bordercolor=D.chrome, lightcolor=D.accent, darkcolor=D.accent)
    style.configure("Dark.TNotebook", background=D.canvas, borderwidth=0, tabmargins=(0, 0, 0, 0),
                    bordercolor=D.canvas, lightcolor=D.canvas, darkcolor=D.canvas)
    style.configure("Dark.TNotebook.Tab", background=D.chrome, foreground=D.secondary, bordercolor=D.separator,
                    lightcolor=D.chrome, darkcolor=D.chrome, padding=(px(14), px(4)),
                    font=fonts.ui)
    style.map("Dark.TNotebook.Tab", background=[("selected", D.group_header)],
              foreground=[("selected", D.label)], lightcolor=[("selected", D.group_header)])
    style.configure("Canvas.TFrame", background=D.canvas)
    style.configure("Dark.Treeview", background=D.list_bg, fieldbackground=D.list_bg, foreground=D.label,
                    bordercolor=D.chrome, lightcolor=D.list_bg, darkcolor=D.list_bg,
                    rowheight=px(22), font=fonts.ui)
    style.map("Dark.Treeview", background=[("selected", "#1f3d63")], foreground=[("selected", D.label)])
    style.configure("Dark.Treeview.Heading", background=D.group_header, foreground=D.label,
                    bordercolor=D.separator, lightcolor=D.group_header, darkcolor=D.group_header,
                    relief="flat", font=fonts.bold)
    style.map("Dark.Treeview.Heading", background=[("active", D.control)])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"Dark.{orient}.TScrollbar", background=D.control, troughcolor=D.chrome,
                        bordercolor=D.chrome, arrowcolor=D.secondary, lightcolor=D.control, darkcolor=D.control)
        style.map(f"Dark.{orient}.TScrollbar", background=[("active", D.hover)])



def enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def dark_title_bar(root: tk.Tk) -> None:
    """系統標題列也用深色（Windows 10 20H1 以後 / macOS），整個視窗才是一致的暗房。"""
    if sys.platform == "win32":
        try:
            import ctypes
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            value = ctypes.c_int(1)
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                              ctypes.sizeof(value)) == 0:
                    break
        except (AttributeError, OSError):
            pass
    elif IS_MAC:
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "appearance", root, "darkaqua")
        except tk.TclError:
            pass

