"""視窗介面：選資料夾 → 量測 → 調門檻（即時更新）→ 搬檔 / 還原。

啟動：python -m astro_light_selector.gui [資料夾]，或打包好的 AstroLightSelector.exe
（把資料夾拖到 exe 上也可以）。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from . import __version__
from .metrics import FITS_SUFFIXES, FrameMetrics
from .mover import restore_rejected, sync_files
from .pipeline import (REJECT_DIR_NAME, REPORT_NAME, Cancelled, Selection, collect_files, decide,
                       measure_files)
from .plot import REJECT_COLOR, draw_decisions
from .report import read_csv, score_summary_lines, write_csv
from .scoring import ScoreConfig

APP_TITLE = "天文 Light 挑片"
ICON = Path(__file__).parent / "assets" / "app.ico"

# 清單欄位：(key, 標題, 寬度 px, 對齊)
COLUMNS = [
    ("file", "檔名", 330, "w"),
    ("result", "結果", 60, "center"),
    ("score", "分數", 55, "e"),
    ("fwhm", "FWHM", 55, "e"),
    ("ecc", "離心率", 60, "e"),
    ("stars", "星點數", 60, "e"),
    ("bkg", "背景", 60, "e"),
    ("group", "分組", 150, "w"),
    ("reason", "原因", 360, "w"),
]


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _fmt(v: float | None, spec: str) -> str:
    return "-" if v is None or v != v else format(v, spec)


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

        self.folder_var = tk.StringVar()
        self.reject_var = tk.StringVar()
        self.method_var = tk.StringVar(value="auto")
        self.pass_var = tk.StringVar(value="80")
        self.min_score_var = tk.StringVar(value="85")
        self.keep_best_var = tk.StringVar(value="70")
        self.group_filter_var = tk.BooleanVar(value=True)
        self.group_exposure_var = tk.BooleanVar(value=True)
        self.group_night_var = tk.BooleanVar(value=False)
        self.workers_var = tk.StringVar(value=str(default_workers()))
        self.only_reject_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="選擇放 light frames 的資料夾")
        self.summary_var = tk.StringVar(value="")

        self._scale = root.winfo_fpixels("1i") / 96.0
        self._build()
        for var in (self.method_var, self.pass_var, self.min_score_var, self.keep_best_var,
                    self.group_filter_var, self.group_exposure_var, self.group_night_var):
            var.trace_add("write", lambda *_: self._schedule_apply())
        self.only_reject_var.trace_add("write", lambda *_: self._fill_table())
        self._update_method_state()
        self._update_buttons()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)
        if folder:
            self._set_folder(Path(folder))

    # ------------------------------------------------------------------ 版面

    def _px(self, v: int) -> int:
        return int(v * self._scale)

    def _build(self) -> None:
        root = self.root
        root.title(f"{APP_TITLE} {__version__}")
        root.geometry(f"{self._px(1280)}x{self._px(900)}")
        root.minsize(self._px(900), self._px(600))
        pad = {"padx": self._px(6), "pady": self._px(4)}

        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Light 資料夾").grid(row=0, column=0, sticky="w")
        # 唯讀：換資料夾一律走「瀏覽…」，才會清掉上一個資料夾的結果，不會拿舊結果去搬新資料夾的檔
        ttk.Entry(top, textvariable=self.folder_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=self._px(4))
        self.browse_btn = ttk.Button(top, text="瀏覽…", command=self._browse_folder)
        self.browse_btn.grid(row=0, column=2)
        ttk.Label(top, text="Reject 資料夾").grid(row=1, column=0, sticky="w", pady=(self._px(4), 0))
        ttk.Entry(top, textvariable=self.reject_var).grid(row=1, column=1, sticky="ew", padx=self._px(4),
                                                          pady=(self._px(4), 0))
        self.browse_reject_btn = ttk.Button(top, text="瀏覽…", command=self._browse_reject)
        self.browse_reject_btn.grid(row=1, column=2, pady=(self._px(4), 0))

        settings = ttk.Frame(root)
        settings.pack(fill="x", **pad)

        th = ttk.LabelFrame(settings, text="keep 門檻")
        th.pack(side="left", fill="y", padx=(0, self._px(8)))
        self.spin_pass = self._method_row(th, 0, "auto", "自動（依整批分布）", self.pass_var, "及格線", "分", 0, 100)
        self.spin_min = self._method_row(th, 1, "min_score", "最低分數", self.min_score_var, "", "分", 0, 100)
        self.spin_keep = self._method_row(th, 2, "keep_best", "只留最好的", self.keep_best_var, "", "%", 1, 100)

        grp = ttk.LabelFrame(settings, text="分組（各組分開算門檻）")
        grp.pack(side="left", fill="y", padx=(0, self._px(8)))
        ttk.Checkbutton(grp, text="濾鏡", variable=self.group_filter_var).pack(anchor="w", padx=self._px(6))
        ttk.Checkbutton(grp, text="曝光時間", variable=self.group_exposure_var).pack(anchor="w", padx=self._px(6))
        ttk.Checkbutton(grp, text="每晚分開", variable=self.group_night_var).pack(anchor="w", padx=self._px(6))

        meas = ttk.LabelFrame(settings, text="量測")
        meas.pack(side="left", fill="y")
        row = ttk.Frame(meas)
        row.pack(anchor="w", padx=self._px(6), pady=self._px(2))
        ttk.Label(row, text="平行處理數").pack(side="left")
        ttk.Spinbox(row, from_=1, to=max(1, os.cpu_count() or 1), width=4,
                    textvariable=self.workers_var).pack(side="left", padx=self._px(4))
        ttk.Label(meas, text="（核心越多越快，但也越吃記憶體）", foreground="gray").pack(anchor="w", padx=self._px(6))

        actions = ttk.Frame(root)
        actions.pack(fill="x", **pad)
        self.measure_btn = ttk.Button(actions, text="開始量測", command=self._start_measure)
        self.measure_btn.pack(side="left")
        self.load_btn = ttk.Button(actions, text="讀取上次結果", command=self._load_report)
        self.load_btn.pack(side="left", padx=self._px(4))
        self.stop_btn = ttk.Button(actions, text="停止", command=self._stop)
        self.stop_btn.pack(side="left")
        self.restore_btn = ttk.Button(actions, text="全部還原…", command=self._restore)
        self.restore_btn.pack(side="right")
        self.move_btn = ttk.Button(actions, text="搬移 reject…", command=self._move)
        self.move_btn.pack(side="right", padx=self._px(4))

        prog = ttk.Frame(root)
        prog.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(prog, textvariable=self.status_var).pack(anchor="w")
        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold", size=bold.cget("size") + 2)
        ttk.Label(prog, textvariable=self.summary_var, font=bold).pack(anchor="w", pady=(self._px(4), 0))

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, **pad)

        plot_tab = ttk.Frame(nb)
        nb.add(plot_tab, text="趨勢圖")
        self.fig = Figure(figsize=(10, 6), layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_tab)
        toolbar = NavigationToolbar2Tk(self.canvas, plot_tab, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._draw_placeholder()

        list_tab = ttk.Frame(nb)
        nb.add(list_tab, text="清單")
        ttk.Checkbutton(list_tab, text="只看 reject", variable=self.only_reject_var).pack(anchor="w")
        table = ttk.Frame(list_tab)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in COLUMNS], show="headings")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=self._px(width), anchor=anchor, stretch=key in ("file", "reason"))
        self.tree.tag_configure("reject", foreground=REJECT_COLOR)
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)

        summary_tab = ttk.Frame(nb)
        nb.add(summary_tab, text="門檻細節")
        self.summary_text = tk.Text(summary_tab, wrap="word", height=10, relief="flat")
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.configure(state="disabled")

    def _method_row(self, parent: ttk.Frame, row: int, value: str, text: str, var: tk.StringVar,
                    prefix: str, unit: str, lo: int, hi: int) -> ttk.Spinbox:
        ttk.Radiobutton(parent, text=text, value=value, variable=self.method_var).grid(
            row=row, column=0, sticky="w", padx=self._px(6), pady=self._px(1))
        if prefix:
            ttk.Label(parent, text=prefix).grid(row=row, column=1, sticky="e")
        spin = ttk.Spinbox(parent, from_=lo, to=hi, width=5, textvariable=var)
        spin.grid(row=row, column=2, padx=self._px(4))
        ttk.Label(parent, text=unit).grid(row=row, column=3, sticky="w", padx=(0, self._px(6)))
        return spin

    def _draw_placeholder(self, text: str = "量測完成後，這裡會顯示各指標的趨勢圖") -> None:
        self.fig.clear()
        self.fig.text(0.5, 0.5, text, ha="center", va="center", color="gray", fontsize=12)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ 狀態

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

    def _update_buttons(self) -> None:
        busy = self._busy()
        folder = self._folder()
        has_folder = folder is not None and folder.is_dir()
        idle = "disabled" if busy else "!disabled"
        self.measure_btn.state([idle if has_folder else "disabled"])
        has_report = has_folder and (folder / REPORT_NAME).is_file()
        self.load_btn.state([idle if has_report else "disabled"])
        self.stop_btn.state(["!disabled" if busy else "disabled"])
        self.move_btn.state([idle if self.selection is not None else "disabled"])
        self.restore_btn.state([idle if has_folder else "disabled"])
        self.browse_btn.state([idle])
        self.browse_reject_btn.state([idle])
        self.measure_btn.configure(text="重新量測" if self.frames else "開始量測")

    def _update_method_state(self) -> None:
        method = self.method_var.get()
        for spin, m in ((self.spin_pass, "auto"), (self.spin_min, "min_score"), (self.spin_keep, "keep_best")):
            spin.state(["!disabled" if m == method else "disabled"])

    # ------------------------------------------------------------------ 資料夾

    def _browse_folder(self) -> None:
        initial = self._folder()
        path = filedialog.askdirectory(title="選擇放 light frames 的資料夾",
                                       initialdir=str(initial) if initial and initial.is_dir() else None)
        if path:
            self._set_folder(Path(path))

    def _browse_reject(self) -> None:
        path = filedialog.askdirectory(title="選擇 reject 要搬去的資料夾")
        if path:
            self.reject_var.set(str(Path(path)))

    def _set_folder(self, folder: Path) -> None:
        if folder.is_file() and folder.suffix.lower() in FITS_SUFFIXES:
            folder = folder.parent  # 拖一張 FITS 進來也行
        self.folder_var.set(str(folder))
        self.reject_var.set(str(folder / REJECT_DIR_NAME))
        self.frames, self.selection = [], None
        self.summary_var.set("")
        self._fill_table()
        self._set_summary_text("")
        self._draw_placeholder()
        self.progress.configure(value=0)
        if not folder.is_dir():
            self.status_var.set(f"找不到資料夾：{folder}")
        elif (folder / REPORT_NAME).is_file():
            self._load_report()
        else:
            n = len(collect_files(folder, self._reject_dir())[0])
            self.status_var.set(f"資料夾裡有 {n} 張 FITS，按「開始量測」" if n else "這個資料夾裡沒有 FITS 檔案")
        self._update_buttons()

    def _load_report(self) -> None:
        folder, reject_dir = self._folder(), self._reject_dir()
        if folder is None or reject_dir is None:
            return
        report = folder / REPORT_NAME
        try:
            frames = read_csv(report, folder)
            files, home_of = collect_files(folder, reject_dir)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"讀取上次結果失敗：\n{exc}")
            return
        now = {Path(home_of.get(str(p), p)).name for p in files}
        # 已經不在資料夾（也不在 reject）的就不算進來，免得影響整批的統計
        kept = [f for f in frames if Path(f.file).name in now]
        new = now - {Path(f.file).name for f in frames}
        self.frames = kept
        self._apply()
        msg = f"已讀取上次的量測結果（{len(kept)} 張），調門檻會即時更新"
        if new:
            msg += f"。注意：有 {len(new)} 張新檔案不在上次結果裡，建議按「重新量測」"
        self.status_var.set(msg)
        self._update_buttons()

    # ------------------------------------------------------------------ 量測

    def _start_measure(self) -> None:
        folder, reject_dir = self._folder(), self._reject_dir()
        if folder is None or reject_dir is None or not folder.is_dir():
            messagebox.showwarning(APP_TITLE, "請先選擇 light 資料夾")
            return
        try:
            workers = max(1, int(self.workers_var.get()))
        except ValueError:
            workers = default_workers()
        files, home_of = collect_files(folder, reject_dir)
        if not files:
            messagebox.showwarning(APP_TITLE, "這個資料夾裡沒有 FITS 檔案")
            return
        self.cancel.clear()
        self.progress.configure(value=0, maximum=len(files))
        extra = f"（含之前 reject 的 {len(home_of)} 張）" if home_of else ""
        self.status_var.set(f"量測中 0/{len(files)}{extra}…")
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
        self.status_var.set("停止中，等正在量的幾張做完…")

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "progress":
            _, i, n, m = event
            self.progress.configure(value=i)
            elapsed = time.monotonic() - self._started
            eta = elapsed / i * (n - i)
            left = f"，約剩 {eta / 60:.0f} 分" if eta >= 90 else (f"，約剩 {eta:.0f} 秒" if i < n else "")
            self.status_var.set(f"量測中 {i}/{n}{left}：{Path(m.file).name}")
        elif kind == "measured":
            self.worker = None
            self.frames = event[1]
            self._apply()
            folder = self._folder()
            report = folder / REPORT_NAME if folder else None
            if report is not None and self.selection is not None:
                try:
                    write_csv(self.selection.decisions, report)
                except OSError as exc:
                    messagebox.showwarning(APP_TITLE, f"報表存不進去：{exc}")
            took = time.monotonic() - self._started
            errors = sum(1 for f in self.frames if f.error)
            self.status_var.set(f"量測完成：{len(self.frames)} 張，花了 {took / 60:.1f} 分"
                                + (f"，其中 {errors} 張量不到星" if errors else "")
                                + f"。結果已存到 {REPORT_NAME}，下次開這個資料夾會直接讀取")
            self._update_buttons()
        elif kind == "cancelled":
            self.worker = None
            self.status_var.set("已停止量測")
            self.progress.configure(value=0)
            self._update_buttons()
        elif kind == "error":
            self.worker = None
            self.status_var.set("量測失敗")
            self._update_buttons()
            messagebox.showerror(APP_TITLE, f"量測時發生錯誤：\n\n{event[1][-1500:]}")

    # ------------------------------------------------------------------ 挑片

    def _schedule_apply(self) -> None:
        self._update_method_state()
        if self._apply_job is not None:
            self.root.after_cancel(self._apply_job)
        self._apply_job = self.root.after(300, self._apply)

    def _config(self) -> ScoreConfig | None:
        """讀介面上的門檻設定；數字還沒打完或不合理時回傳 None。"""
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
        except ValueError:
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
            self.status_var.set("門檻數字不合理：分數要在 0~100，只留最好的要在 1~100%")
            return
        self.selection = sel = decide(self.frames, self._group_keys(), "score", cfg)
        n = len(sel.decisions)
        th = sel.thresholds
        th_text = f"，門檻 {next(iter(th.values())):.1f} 分" if len(th) == 1 else (f"，{len(th)} 組各自算門檻" if th else "")
        self.summary_var.set(f"共 {n} 張：keep {sel.n_keep}，reject {n - sel.n_keep}{th_text}")
        lines: list[str] = []
        for label, size in sel.group_sizes.items():
            lines.append(f"【{label or '全部'}】{size} 張")
            result = sel.results.get(label)
            lines += score_summary_lines(result) if result else ["這組沒有任何一張能量測，全部 reject"]
            lines.append("")
        self._set_summary_text("\n".join(lines))
        draw_decisions(self.fig, sel.decisions, th)
        self.canvas.draw_idle()
        self._fill_table()
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
        for d in rows:
            if self.only_reject_var.get() and d.keep:
                continue
            m = d.metrics
            self.tree.insert("", "end", tags=() if d.keep else ("reject",), values=(
                Path(m.file).name, "keep" if d.keep else "reject", _fmt(d.score, ".1f"),
                _fmt(m.fwhm, ".2f"), _fmt(m.eccentricity, ".2f"), m.n_stars, _fmt(m.background, ".0f"),
                d.group, "; ".join(d.reasons)))
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
            messagebox.showinfo(APP_TITLE, "檔案位置已經跟目前的結果一致，不用搬。")
            return
        lines = []
        if out:
            lines.append(f"把 {len(out)} 張 reject 搬到：\n{reject_dir}")
        if back:
            lines.append(f"把 {len(back)} 張之前 reject、現在變成 keep 的搬回原位")
        if not messagebox.askyesno(APP_TITLE, "\n\n".join(lines) + "\n\n確定要搬嗎？之後可以用「全部還原」搬回來。"):
            return
        try:
            out, back = sync_files(sel.decisions, reject_dir)
            write_csv(sel.decisions, folder / REPORT_NAME)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"搬移時發生錯誤：\n{exc}\n\n已經搬好的有記錄，可以用「全部還原」搬回。")
            return
        parts = []
        if out:
            parts.append(f"已搬移 {len(out)} 張 reject")
        if back:
            parts.append(f"已搬回 {len(back)} 張")
        self.status_var.set("，".join(parts) + f"（{reject_dir}）")

    def _restore(self) -> None:
        reject_dir, folder = self._reject_dir(), self._folder()
        if reject_dir is None or folder is None:
            return
        n = len(restore_rejected(reject_dir, folder, dry_run=True))
        if n == 0:
            messagebox.showinfo(APP_TITLE, "Reject 資料夾裡沒有可以搬回的檔案。")
            return
        if not messagebox.askyesno(APP_TITLE, f"把 {reject_dir} 裡的 {n} 張 FITS 全部搬回原位？\n"
                                              "（手動放進去的也會一起搬回 light 資料夾）"):
            return
        try:
            restored = restore_rejected(reject_dir, folder)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"搬回時發生錯誤：\n{exc}")
            return
        self.status_var.set(f"已搬回 {len(restored)} 張")

    def _on_close(self) -> None:
        if self._busy():
            if not messagebox.askyesno(APP_TITLE, "還在量測中，確定要關閉嗎？"):
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


def _setup_fonts(root: tk.Tk) -> None:
    families = set(font.families(root))
    family = next((f for f in ("Microsoft JhengHei UI", "Microsoft JhengHei") if f in families), None)
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        f = font.nametofont(name)
        if family:
            f.configure(family=family)
        f.configure(size=10)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # 打包成沒有主控台的 exe 時 stdout / stderr 是 None，有人 print 就會當掉
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    _enable_dpi_awareness()
    root = tk.Tk()
    _setup_fonts(root)
    if ICON.is_file():
        try:
            root.iconbitmap(default=str(ICON))
        except tk.TclError:
            pass
    App(root, argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
