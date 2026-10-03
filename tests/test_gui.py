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
    """建立 PickView；測試結束時停掉它的計時器、刪掉它，下一個測試再用同一個 Tk。"""
    from astro_light_selector import gui

    apps = []

    def make(folder):
        app = gui.PickView(tk_root, tk_root, str(folder))
        app.pack(fill="both", expand=True)
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.close()
        app.destroy()


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
    _pump(root, lambda: app.selection is not None and not app.is_busy())

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
    _pump(root, lambda: app.selection is not None and not app.is_busy())
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
    _pump(root, lambda: app.selection is not None and not app.is_busy())
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


def test_gui_view_is_embeddable(tmp_path: Path, tk_root):
    """PickView 要能嵌進整合版的分頁：不綁全域事件、重建與關閉只動自己，語言切換可以交給外面。"""
    from astro_light_selector import gui, i18n

    make_session(tmp_path, n_good=3)
    neighbour = tk.Frame(tk_root)  # 同一個視窗裡別套的畫面
    neighbour.pack()
    asked: list[str] = []
    view = gui.PickView(tk_root, tk_root, str(tmp_path), on_language=asked.append)
    view.pack(fill="both", expand=True)
    try:
        for sequence in ("<Button-1>", "<Escape>", "<MouseWheel>", "<Control-o>"):
            assert not tk_root.bind_all(sequence)

        view._set_language("en")
        _pump(tk_root, lambda: asked == ["en"])
        assert i18n.get_language() == "zh"  # 交給外面決定，View 自己不換

        view.rebuild()
        assert neighbour.winfo_exists()
        assert view.is_busy() is False
    finally:
        view.close()
        view.destroy()
    assert neighbour.winfo_exists()
    neighbour.destroy()

    quiet = gui.PickView(tk_root, tk_root, show_language=False)
    try:
        assert not any(isinstance(w, gui.Segmented) and "zh" in w.labels for w in _descendants(quiet))
    finally:
        quiet.close()
        quiet.destroy()


def _descendants(widget: tk.Misc) -> list[tk.Misc]:
    out = []
    for child in widget.winfo_children():
        out += [child, *_descendants(child)]
    return out


def test_gui_custom_weights(tmp_path: Path, make_app, isolated_settings: Path):
    import json

    make_session(tmp_path)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app.is_busy())
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
    _pump(root, lambda: app.selection is not None and not app.is_busy())
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
    _pump(root, lambda: app.selection is not None and not app.is_busy())

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

    monkeypatch.setattr(gui.PickView, "WATCH_INTERVAL_MS", 600_000)  # 測試自己呼叫 _watch_tick
    make_session(tmp_path, n_good=3)
    app = make_app(tmp_path)
    root = app.root
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app.is_busy())
    before = len(app.frames)

    app.watch_var.set(True)
    assert "監看中" in app.status_var.get()
    new = write_fits(tmp_path / "new.fits", make_star_field(fwhm=3.0, seed=99), date_obs="2026-09-15T12:00:00")
    app._watch_tick()  # 第一次只記下檔案大小，可能還在寫
    assert not app.is_busy() and len(app.frames) == before
    app._watch_tick()  # 大小沒變：寫完了，開始量
    _pump(root, lambda: not app.is_busy() and len(app.frames) == before + 1)
    assert new.name in {Path(d.metrics.file).name for d in app.selection.decisions}
    assert new.name in (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert "新增 1 張" in app.status_var.get()
    app._watch_tick()  # 已經量過的不會再量
    assert not app.is_busy()

    app.watch_var.set(False)
    assert app._watch_job is None


def test_gui_subfolders(tmp_path: Path, make_app, dialogs):
    nights = {name: make_session(tmp_path / "iso800" / name, n_good=4) for name in ("date_0101", "date_0202")}
    app = make_app(tmp_path)
    root = app.root
    assert app.recursive_var.get()  # 最上層本身沒有影像：自動包含子資料夾
    assert "2 個資料夾" in app.status_var.get()
    app.workers_var.set("1")
    app._start_measure()
    _pump(root, lambda: app.selection is not None and not app.is_busy())
    assert len(app.selection.decisions) == 14
    assert "folder" in app.tree["displaycolumns"]
    key = "iso800/date_0202/light_000.fits"  # 兩晚的檔名一樣，清單用相對路徑分開
    assert app.tree.exists(key) and app.tree.exists("iso800/date_0101/light_000.fits")
    assert app.tree.set(key, "folder") == "iso800/date_0202"

    # 手動覆寫用相對路徑，只影響那一晚的那一張
    app.tree.selection_set(key)
    app._set_override("reject")
    assert {d.metrics.file for d in app.selection.decisions if d.override} == {str(tmp_path / key)}

    app._move()
    assert "date_0101：3 張" in dialogs["asked"][-1] and "date_0202：4 張" in dialogs["asked"][-1]
    for name, files in nights.items():
        rejected = {p.name for p in (tmp_path / "iso800" / name / "rejected").glob("*.fits")}
        expected = {p.name for p in files["bad"]} | ({"light_000.fits"} if name == "date_0202" else set())
        assert rejected == expected
    assert not (tmp_path / "rejected").exists()

    # 搬走的片也看得到預覽
    app.tree.selection_set(key)
    _pump(root, lambda: key in app._previews)

    # 重新開同一個資料夾：報表有子資料夾路徑，照樣包含子資料夾、讀回上次的結果
    app.open_folder(tmp_path)
    assert app.recursive_var.get() and app.selection is not None and len(app.selection.decisions) == 14

    app._restore()
    for files in nights.values():
        assert all(p.exists() for p in files["good"] + files["bad"])


def _measured(make_app, folder: Path):
    app = make_app(folder)
    app.workers_var.set("1")
    app._start_measure()
    _pump(app.root, lambda: app.selection is not None and not app.is_busy())
    return app


def test_gui_slider_number_entry(tmp_path: Path, make_app):
    from astro_light_selector.darkroom import ParameterSlider

    make_session(tmp_path, n_good=3)
    app = _measured(make_app, tmp_path)

    slider = app.sliders["auto"]
    slider.start_edit()
    assert slider.entry.get() == "80"
    slider.entry.delete(0, "end")
    slider.entry.insert(0, "120 分")  # 超出範圍：夾到上限
    slider.commit_edit()
    assert app.pass_var.get() == 100 and slider.entry is None
    slider.start_edit()
    slider.entry.insert(0, "abc")  # 讀不出數字：照舊
    slider.cancel_edit()
    assert app.pass_var.get() == 100

    # 整數的設定存回整數
    var = tk.IntVar(master=app, value=2)
    workers = ParameterSlider(app, app, "t", var, 1, 8, lambda v: f"{v:.0f}")
    workers.start_edit()
    workers.entry.delete(0, "end")
    workers.entry.insert(0, "5.6")
    workers.commit_edit()
    assert var.get() == 6
    workers.destroy()

    # 權重打的是百分比：FWHM 改成 50%，其他幾項維持彼此的比例
    fwhm = app.weight_sliders["fwhm"]
    fwhm.start_edit()
    assert fwhm.entry.get() == "35"
    fwhm.entry.delete(0, "end")
    fwhm.entry.insert(0, "50%")
    fwhm.commit_edit()
    weights = {m: v.get() for m, v in app.weight_vars.items()}
    total = sum(weights.values())
    assert weights["fwhm"] / total == pytest.approx(0.5, abs=1e-3)
    assert weights["eccentricity"] / weights["n_stars"] == pytest.approx(30 / 20, abs=1e-3)
    assert fwhm.value.cget("text") == "50%"


def test_gui_keys_exposure_and_plot_click(tmp_path: Path, make_app):
    from types import SimpleNamespace

    files = make_session(tmp_path)
    app = _measured(make_app, tmp_path)
    # 保留 8 張 × 120 秒
    assert app.metrics["kept_exposure"].value.cget("text") == "16m"
    assert "保留片總曝光：16m（8 張）" in app.summary_text.get("1.0", "end")

    # 趨勢圖點一下：切到清單、選取那一張
    assert app._plot_order
    i = next(i for i, d in enumerate(app._plot_order) if d.keep)
    key = Path(app._plot_order[i].metrics.file).name
    app.only_reject_var.set(True)  # 只看淘汰片時點到保留片：先關掉才找得到
    app._plot_clicked(SimpleNamespace(button=1, inaxes=object(), xdata=float(i)))
    assert app.nb.index("current") == 1 and app.tree.selection() == (key,)
    assert not app.only_reject_var.get()

    # K / X / A：選一張按 X 變成強制淘汰，接著選下一張
    items = app.tree.get_children()
    first, second = items[0], items[1]
    app.tree.selection_set(first)
    assert app._key_override(SimpleNamespace(state=0x4), "reject") is None  # 按著 Ctrl 不算
    assert first not in app.overrides
    app._key_override(SimpleNamespace(state=0), "reject")
    assert app.overrides[first] == "reject" and app.tree.selection() == (second,)
    app._key_override(SimpleNamespace(state=0), "keep")
    assert app.overrides[second] == "keep"
    app.tree.selection_set(first)
    app._key_override(SimpleNamespace(state=0), None)
    assert first not in app.overrides
    # 在清單分頁時趨勢圖先不重畫，切回去才畫
    assert app._plot_dirty
    app.nb.select(0)
    app.root.update()
    assert not app._plot_dirty
    assert files


def test_gui_report_matched_by_file_name(tmp_path: Path, make_app):
    import shutil

    for name in ("n1", "n2"):
        for p in make_session(tmp_path / name, n_good=3)["good"] + list((tmp_path / name).glob("bad_*.fits")):
            p.rename(p.with_name(f"{name}_{p.name}"))
    app = _measured(make_app, tmp_path)
    assert app.recursive_var.get() and len(app.frames) == 12
    key = "n1/n1_bad_cloud.fits"
    app.tree.selection_set(key)
    app._set_override("keep")

    # 檔案被攤平到同一層：不包含子資料夾，量測結果和手動覆寫都依檔名認回
    for name in ("n1", "n2"):
        for p in (tmp_path / name).glob("*.fits"):
            shutil.move(str(p), str(tmp_path / p.name))
        shutil.rmtree(tmp_path / name)
    app.open_folder(tmp_path)
    assert not app.recursive_var.get()
    assert len(app.selection.decisions) == 12
    assert "12 張的位置變了" in app.status_var.get()
    assert app.overrides == {"n1_bad_cloud.fits": "keep"}
    assert "n1/" not in (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")


def test_gui_organize_and_undo(tmp_path: Path, make_app, dialogs):
    from .synth import make_star_field, write_fits

    make_session(tmp_path, n_good=4)
    for i in range(3):  # 同一個資料夾混了另一台相機拍的 Ha
        write_fits(tmp_path / f"ha_{i}.fits", make_star_field(seed=50 + i), filter="Ha", instrume="ZWO ASI2600MM Pro")
    app = _measured(make_app, tmp_path)
    app.tree.selection_set("ha_0.fits")
    app._set_override("reject")
    for key, var in app.organize_vars.items():
        var.set(key in ("camera", "filter"))

    app._organize()
    assert "UnknownCamera/L：7 張" in dialogs["asked"][-1] and "ZWO ASI2600MM Pro/Ha：3 張" in dialogs["asked"][-1]
    assert (tmp_path / "ZWO ASI2600MM Pro" / "Ha" / "ha_0.fits").is_file()
    assert app.recursive_var.get() and len(app.selection.decisions) == 10
    assert app.overrides == {"ZWO ASI2600MM Pro/Ha/ha_0.fits": "reject"}
    assert "ZWO ASI2600MM Pro/Ha/ha_0.fits" in (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")

    # 重開資料夾照樣讀得到；有淘汰片搬走時要先還原才能復原整理
    app.open_folder(tmp_path)
    assert app.recursive_var.get() and len(app.selection.decisions) == 10
    app._move()
    app._undo_organize()
    assert (tmp_path / "ZWO ASI2600MM Pro" / "Ha" / "rejected").is_dir()  # 沒動
    app._restore()

    app._undo_organize()
    assert (tmp_path / "ha_0.fits").is_file() and not (tmp_path / "ZWO ASI2600MM Pro").exists()
    assert not app.recursive_var.get() and app.overrides == {"ha_0.fits": "reject"}


def test_gui_subfolders_custom_reject_dir(tmp_path: Path, make_app, dialogs, isolated_settings: Path):
    nights = {name: make_session(tmp_path / "light" / name, n_good=4) for name in ("0101", "0202")}
    app = _measured(make_app, tmp_path / "light")
    target = tmp_path / "second pass"
    app._set_reject(target)
    assert "照原本的子資料夾結構放" in app.reject_label.cget("text")
    app._move()
    assert "0101：3 張" in dialogs["asked"][-1]
    for name, files in nights.items():
        assert sorted(p.name for p in (target / name).glob("*.fits")) == sorted(p.name for p in files["bad"])

    # 每個資料夾分開記住：重開時找得到搬過去的片
    app.open_folder(tmp_path / "light")
    assert app.reject_var.get() == str(target) and len(app.selection.decisions) == 14
    app._restore()
    assert all(p.exists() for files in nights.values() for p in files["good"] + files["bad"])
    assert not target.exists()
    app._set_reject(None)
    assert app._reject_dir() is None and app.reject_label.cget("text") == "各資料夾裡的 rejected"
