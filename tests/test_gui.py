"""視窗介面的流程測試：不點滑鼠，直接呼叫按鈕背後的方法，確認量測 → 調門檻 → 搬移 → 還原都對。"""

import time
import tkinter as tk
from pathlib import Path

import pytest

from .synth import make_session


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("沒有圖形環境")
    r.withdraw()
    yield r
    r.destroy()


def _pump(root: tk.Tk, cond, timeout: float = 180) -> None:
    """跑 Tk 的事件迴圈直到 cond() 成立。"""
    end = time.monotonic() + timeout
    while not cond():
        root.update()
        if time.monotonic() > end:
            raise TimeoutError
        time.sleep(0.02)


def test_gui_measure_adjust_move_restore(tmp_path: Path, root: tk.Tk, monkeypatch):
    from astro_light_selector import gui

    files = make_session(tmp_path)
    asked: list[str] = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda title, msg: asked.append(msg) or True)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg: pytest.fail(msg))

    app = gui.App(root, str(tmp_path))
    assert app.selection is None
    app.workers_var.set("2")  # 走多核心那條路
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())

    assert (tmp_path / "selection_report.csv").is_file()
    assert len(app.selection.decisions) == len(files["good"]) + len(files["bad"])
    rejected = {Path(d.metrics.file).name for d in app.selection.decisions if not d.keep}
    assert rejected == {p.name for p in files["bad"]}
    assert "reject 3" in app.summary_var.get()
    assert len(app.tree.get_children()) == 11
    app.only_reject_var.set(True)
    assert len(app.tree.get_children()) == 3

    # 改成最低分數 0：量得出來的都變 keep，畫面在延遲後自動更新
    app.method_var.set("min_score")
    app.min_score_var.set("0")
    _pump(root, lambda: app._apply_job is None)
    assert all(d.keep for d in app.selection.decisions if d.metrics.error is None)

    # 改回自動門檻後搬移
    app.method_var.set("auto")
    _pump(root, lambda: app._apply_job is None)
    app._move()
    assert "3 張 reject" in asked[-1]
    assert sorted(p.name for p in (tmp_path / "rejected").glob("*.fits")) == sorted(p.name for p in files["bad"])

    app._restore()
    assert all(p.exists() for p in files["good"] + files["bad"])

    # 重新打開同一個資料夾：直接讀上次的結果，不用重新量
    app._set_folder(tmp_path)
    assert app.selection is not None and len(app.selection.decisions) == 11
    assert "已讀取上次的量測結果" in app.status_var.get()


def test_gui_bad_threshold_input_is_ignored(tmp_path: Path, root: tk.Tk):
    from astro_light_selector import gui

    make_session(tmp_path, n_good=3)
    app = gui.App(root, str(tmp_path))
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())
    before = app.selection

    app.method_var.set("keep_best")
    app.keep_best_var.set("abc")  # 還沒打完的數字
    _pump(root, lambda: app._apply_job is None)
    assert app.selection is before
    assert "門檻數字不合理" in app.status_var.get()
