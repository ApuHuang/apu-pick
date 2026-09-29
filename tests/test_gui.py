"""視窗介面的流程測試：不點滑鼠，直接呼叫按鈕背後的方法，確認量測 → 調門檻 → 搬移 → 還原都對。"""

import time
import tkinter as tk
from pathlib import Path

import pytest

from .synth import make_session


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    """測試不能動到使用者真正的設定檔，也要把語言還原成預設。"""
    from astro_light_selector import i18n, settings

    cfg = tmp_path_factory.mktemp("config")
    monkeypatch.setattr(settings, "config_dir", lambda: cfg)
    yield cfg
    i18n.set_language(i18n.DEFAULT_LANGUAGE)


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """所有對話框都換成假的：沒人能按的雲端機器上，真的對話框會讓測試永遠卡住。

    詢問一律回答「是」並記下內容；錯誤、警告記下來，_pump 看到就讓測試失敗。
    """
    from astro_light_selector import gui

    _ERRORS.clear()
    record = {"asked": [], "errors": _ERRORS}
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda title, msg: record["asked"].append(msg) or True)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda title, msg: _ERRORS.append(msg))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg: _ERRORS.append(msg))
    return record


_ERRORS: list[str] = []


@pytest.fixture(scope="session")
def tk_root():
    """整個測試只建一個 Tk：同一個行程反覆建立、關閉 Tk，Windows 偶爾找不到 tk.tcl，Mac 上會卡住。"""
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"沒有圖形環境：{exc}")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def make_app(tk_root):
    """建立 App；測試結束時停掉它的計時器、清掉畫面，下一個測試再用同一個 Tk。"""
    from astro_light_selector import gui

    apps = []

    def make(folder):
        app = gui.App(tk_root, str(folder))
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.close()


def _pump(root: tk.Tk, cond, timeout: float = 180) -> None:
    """跑 Tk 的事件迴圈直到 cond() 成立；程式跳出錯誤對話框就直接失敗。"""
    end = time.monotonic() + timeout
    while not cond():
        root.update()
        if _ERRORS:
            pytest.fail("程式顯示了錯誤：\n" + "\n".join(_ERRORS))
        if time.monotonic() > end:
            raise TimeoutError
        time.sleep(0.02)


def test_gui_measure_adjust_move_restore(tmp_path: Path, make_app, dialogs):
    files = make_session(tmp_path)
    app = make_app(tmp_path)
    root = app.root
    assert app.selection is None
    app.workers_var.set("2")  # 走多核心那條路
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())

    assert (tmp_path / "selection_report.csv").is_file()
    assert len(app.selection.decisions) == len(files["good"]) + len(files["bad"])
    rejected = {Path(d.metrics.file).name for d in app.selection.decisions if not d.keep}
    assert rejected == {p.name for p in files["bad"]}
    assert "淘汰 3" in app.summary_var.get()
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
    assert "3 張淘汰片" in dialogs["asked"][-1]
    assert sorted(p.name for p in (tmp_path / "rejected").glob("*.fits")) == sorted(p.name for p in files["bad"])

    app._restore()
    assert all(p.exists() for p in files["good"] + files["bad"])

    # 重新打開同一個資料夾：直接讀上次的結果，不用重新量
    app._set_folder(tmp_path)
    assert app.selection is not None and len(app.selection.decisions) == 11
    assert "已讀取上次的量測結果" in app.status_var.get()


def test_gui_bad_threshold_input_is_ignored(tmp_path: Path, make_app):
    make_session(tmp_path, n_good=3)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())
    before = app.selection

    app.method_var.set("keep_best")
    app.keep_best_var.set(0)  # 一張都不留：不合理
    _pump(root, lambda: app._apply_job is None)
    assert app.selection is before
    assert "門檻數字不合理" in app.status_var.get()


def test_gui_switch_language_keeps_results(tmp_path: Path, make_app, isolated_settings: Path):
    import json

    from astro_light_selector import i18n

    files = make_session(tmp_path, n_good=5)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())
    n_reject = sum(not d.keep for d in app.selection.decisions)
    assert f"淘汰 {n_reject}" in app.summary_var.get()

    app._set_language("en")
    _pump(root, lambda: i18n.get_language() == "en" and app.measure_btn.winfo_exists())
    # 結果沒丟，文字全換成英文
    assert len(app.selection.decisions) == len(files["good"]) + len(files["bad"])
    assert f"{n_reject} rejected" in app.summary_var.get()
    assert app.status_var.get().startswith("Done:")
    assert app.measure_btn.cget("text") == "Measure Again"
    assert app.metrics["rejected"].value.cget("text") == str(n_reject)
    first = app.tree.get_children()[0]
    assert app.tree.set(first, "result") in ("Keep", "Reject")
    reasons = [app.tree.set(i, "reason") for i in app.tree.get_children() if app.tree.set(i, "reason")]
    assert reasons and all("分數" not in r and "弱項" not in r for r in reasons)
    assert json.loads((isolated_settings / "settings.json").read_text(encoding="utf-8"))["language"] == "en"

    app._set_language("zh")
    _pump(root, lambda: i18n.get_language() == "zh" and app.measure_btn.winfo_exists())
    assert f"淘汰 {n_reject}" in app.summary_var.get()


def test_gui_custom_weights(tmp_path: Path, make_app, isolated_settings: Path):
    import json

    make_session(tmp_path)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())
    default_scores = {d.metrics.file: d.score for d in app.selection.decisions}
    assert app.weight_badge.cget("text") == "建議權重"
    assert app.weight_sliders["fwhm"].value.cget("text") == "35%"

    # 只看 FWHM：分數改變，弱項只可能是 FWHM，標示變成自訂
    for m in ("eccentricity", "n_stars", "background"):
        app.weight_vars[m].set(0)
    _pump(root, lambda: app._apply_job is None)
    assert app.weight_badge.cget("text") == "自訂權重"
    assert app.weight_sliders["fwhm"].value.cget("text") == "100%"
    assert {d.metrics.file: d.score for d in app.selection.decisions} != default_scores
    reasons = [str(r) for d in app.selection.decisions for r in d.reasons if r.code == "score_low"]
    assert all("弱項：FWHM" in r for r in reasons)
    assert "FWHM 100%" in app.summary_text.get("1.0", "end")
    saved = json.loads((isolated_settings / "settings.json").read_text(encoding="utf-8"))
    assert saved["weights"]["eccentricity"] == 0

    # 全部設成 0：不重算，提示使用者
    before = app.selection
    app.weight_vars["fwhm"].set(0)
    _pump(root, lambda: app._apply_job is None)
    assert app.selection is before
    assert "權重不能全部是 0" in app.status_var.get()

    # 恢復建議值：回到原本的分數
    app._reset_weights()
    _pump(root, lambda: app._apply_job is None)
    assert app.weight_badge.cget("text") == "建議權重"
    assert {d.metrics.file: d.score for d in app.selection.decisions} == default_scores


def test_gui_manual_override(tmp_path: Path, make_app):
    import csv

    files = make_session(tmp_path)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())
    bad, good = files["bad"][0].name, files["good"][0].name
    by_name = lambda: {Path(d.metrics.file).name: d for d in app.selection.decisions}  # noqa: E731
    assert not by_name()[bad].keep and by_name()[good].keep

    # 強制保留一張淘汰的、強制淘汰一張保留的
    app.tree.selection_set(bad)
    app._set_override("keep")
    app.tree.selection_set(good)
    app._set_override("reject")
    assert by_name()[bad].keep and by_name()[bad].override == "keep"
    assert not by_name()[good].keep and str(by_name()[good].reasons[0]) == "手動淘汰"
    assert app.tree.set(bad, "result").startswith("✎")
    assert app.metrics["manual"].value.cget("text") == "2"
    with (tmp_path / "selection_report.csv").open(encoding="utf-8-sig", newline="") as fh:
        saved = {r["file"]: r["override"] for r in csv.DictReader(fh)}
    assert saved[bad] == "keep" and saved[good] == "reject"

    # 調門檻、重開資料夾都不會洗掉手動決定
    app.method_var.set("min_score")
    app.min_score_var.set(0)
    _pump(root, lambda: app._apply_job is None)
    assert not by_name()[good].keep
    app._set_folder(tmp_path)
    assert by_name()[bad].keep and not by_name()[good].keep

    # 改回自動
    app.tree.selection_set(good)
    app._set_override(None)
    assert by_name()[good].keep and by_name()[good].override is None


def test_gui_preview(tmp_path: Path, make_app):
    files = make_session(tmp_path)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())

    name = files["good"][0].name
    app.tree.selection_set(name)
    _pump(root, lambda: name in app._previews and getattr(app, "_thumb_image", None) is not None)
    assert app.preview_title.cget("text") == name
    assert app.preview_closeup.cget("image")

    # 搬到淘汰片資料夾的片也要看得到
    app._move()
    bad = files["bad"][0].name
    assert not (tmp_path / bad).exists()
    app.tree.selection_set(bad)
    _pump(root, lambda: bad in app._previews)


def test_gui_watch_folder_measures_new_files(tmp_path: Path, make_app, monkeypatch):
    from astro_light_selector import gui

    from .synth import make_star_field, write_fits

    monkeypatch.setattr(gui.App, "WATCH_INTERVAL_MS", 600_000)  # 測試自己呼叫 _watch_tick
    make_session(tmp_path, n_good=3)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app._busy())
    before = len(app.frames)

    app.watch_var.set(True)
    assert "監看中" in app.status_var.get()
    new = write_fits(tmp_path / "new.fits", make_star_field(fwhm=3.0, seed=99), date_obs="2026-09-15T12:00:00")
    app._watch_tick()  # 第一次只記下檔案大小，可能還在寫
    assert not app._busy() and len(app.frames) == before
    app._watch_tick()  # 大小沒變：寫完了，開始量
    _pump(root, lambda: not app._busy() and len(app.frames) == before + 1)
    assert new.name in {Path(d.metrics.file).name for d in app.selection.decisions}
    assert new.name in (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert "新增 1 張" in app.status_var.get()
    app._watch_tick()  # 已經量過的不會再量
    assert not app._busy()

    app.watch_var.set(False)
    assert app._watch_job is None
