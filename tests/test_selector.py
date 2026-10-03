from pathlib import Path

import pytest

from astro_light_selector.cli import main
from astro_light_selector.metrics import measure
from astro_light_selector.selector import Thresholds, select

from .synth import make_session, make_star_field, write_fits


def test_measure_fwhm_and_star_count(tmp_path: Path):
    path = write_fits(tmp_path / "a.fits", make_star_field(fwhm=3.0, n_stars=150))
    m = measure(path)
    assert m.error is None
    assert 2.8 <= m.fwhm <= 3.2
    assert 100 <= m.n_stars <= 150
    assert m.eccentricity < 0.4
    assert 750 <= m.background <= 850
    assert m.exposure == 120.0
    assert m.filter == "L"


def test_measure_detects_elongation(tmp_path: Path):
    path = write_fits(tmp_path / "e.fits", make_star_field(elongation=2.5))
    m = measure(path)
    assert m.eccentricity > 0.7


def test_measure_bayer_scales_fwhm(tmp_path: Path):
    path = write_fits(tmp_path / "osc.fits", make_star_field(fwhm=4.0), bayerpat="RGGB")
    m = measure(path)
    assert m.error is None
    assert 3.5 <= m.fwhm <= 4.5


def test_measure_unreadable(tmp_path: Path):
    bad = tmp_path / "junk.fits"
    bad.write_bytes(b"not a fits file")
    m = measure(bad)
    assert m.error is not None


def test_select_flags_bad_frames(tmp_path: Path):
    files = make_session(tmp_path)
    frames = [measure(p) for p in files["good"] + files["bad"]]
    decisions = {Path(d.metrics.file).name: d for d in select(frames, Thresholds())}

    for p in files["good"]:
        assert decisions[p.name].keep, decisions[p.name].reasons
    for p in files["bad"]:
        assert not decisions[p.name].keep, p.name


def test_cli_moves_rejected_and_writes_report(tmp_path: Path):
    files = make_session(tmp_path)
    assert main([str(tmp_path)]) == 0

    rejected = tmp_path / "rejected"
    assert sorted(p.name for p in rejected.glob("*.fits")) == sorted(p.name for p in files["bad"])
    for p in files["good"]:
        assert p.exists()

    report = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert report.count("reject") == len(files["bad"])
    assert "bad_seeing.fits" in report


def test_cli_dry_run_does_not_move(tmp_path: Path):
    files = make_session(tmp_path)
    assert main([str(tmp_path), "--dry-run"]) == 0
    assert not (tmp_path / "rejected").exists()
    for p in files["bad"]:
        assert p.exists()


def test_score_mode_flags_bad_frames(tmp_path: Path):
    from astro_light_selector.scoring import ScoreConfig
    from astro_light_selector.selector import select_by_score

    files = make_session(tmp_path, n_good=12)
    frames = [measure(p) for p in files["good"] + files["bad"]]
    decisions, result = select_by_score(frames, ScoreConfig())
    by_name = {Path(d.metrics.file).name: d for d in decisions}

    assert 0 < result.threshold <= 100
    for p in files["good"]:
        assert by_name[p.name].keep, by_name[p.name].reasons
        assert by_name[p.name].score is not None and by_name[p.name].score >= result.threshold
    for p in files["bad"]:
        assert not by_name[p.name].keep, p.name


def test_cli_score_mode_writes_score_column(tmp_path: Path):
    make_session(tmp_path)
    assert main([str(tmp_path), "--dry-run", "--mode", "score"]) == 0
    report = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    header = report.splitlines()[0]
    assert "score" in header.split(",")


def test_group_by_filter_scores_each_filter_separately(tmp_path: Path):
    from astro_light_selector.grouping import group_frames
    from astro_light_selector.scoring import ScoreConfig
    from astro_light_selector.selector import select_by_score

    # Ha 背景低、星少很多；混在一起算的話整組 Ha 會被當成爛片
    for i in range(6):
        write_fits(tmp_path / f"L_{i}.fits", make_star_field(seed=i, n_stars=200, background=2000), filter="L")
        write_fits(tmp_path / f"Ha_{i}.fits", make_star_field(seed=50 + i, n_stars=60, background=300), filter="Ha")
    frames = [measure(p) for p in sorted(tmp_path.glob("*.fits"))]

    groups = group_frames(frames, ("filter",))
    assert sorted(groups) == ["濾鏡 Ha", "濾鏡 L"]
    for members in groups.values():
        decisions, _ = select_by_score(members, ScoreConfig())
        assert all(d.keep for d in decisions), [d.reasons for d in decisions if not d.keep]


def test_assign_nights_splits_on_gap():
    from astro_light_selector.grouping import assign_nights
    from astro_light_selector.metrics import FrameMetrics

    def fm(name: str, t: str) -> FrameMetrics:
        return FrameMetrics(name, 100, 3.0, 0.2, 800, 10, 20, 0, 120.0, "L", t)

    # 一晚跨過 UTC 午夜仍算同一晚
    frames = [fm("a", "2026-09-05T22:00:00"), fm("b", "2026-09-06T01:30:00"),
              fm("c", "2026-09-06T21:00:00"), fm("d", None)]
    nights = assign_nights(frames)
    assert nights == {"a": "2026-09-05", "b": "2026-09-05", "c": "2026-09-06"}


def test_cli_restore_moves_files_back(tmp_path: Path):
    files = make_session(tmp_path)
    assert main([str(tmp_path)]) == 0
    assert all(not p.exists() for p in files["bad"])

    assert main([str(tmp_path), "--restore"]) == 0
    for p in files["good"] + files["bad"]:
        assert p.exists()
    assert not list((tmp_path / "rejected").glob("*.fits"))


def test_macos_appledouble_files_are_ignored(tmp_path: Path):
    """exFAT 隨身碟上 macOS 會留下「._檔名.fit」，不能當成影像量測，也不能被還原搬走。"""
    from astro_light_selector.pipeline import collect_files

    files = make_session(tmp_path)
    junk = [tmp_path / f"._{p.name}" for p in files["good"][:2]]
    for p in junk:
        p.write_bytes(b"\x00\x05\x16\x07")
    found, _ = collect_files(tmp_path, tmp_path / "rejected")
    assert not any(p.name.startswith("._") for p in found)

    rejected = tmp_path / "rejected"
    rejected.mkdir()
    (rejected / "._light_999.fits").write_bytes(b"\x00\x05\x16\x07")
    assert main([str(tmp_path), "--restore"]) == 0
    assert not (tmp_path / "._light_999.fits").exists()


def test_cli_from_report_after_move_does_not_fail(tmp_path: Path):
    make_session(tmp_path)
    assert main([str(tmp_path)]) == 0
    report = tmp_path / "selection_report.csv"
    # 檔案已經搬走了，重套門檻不應該出錯
    assert main([str(tmp_path), "--from-report", str(report), "--report", str(tmp_path / "r2.csv")]) == 0


def test_cli_rejects_unknown_weight(tmp_path: Path):
    make_session(tmp_path, n_good=3)
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--dry-run", "--weights", "fwhm=1,bogus=2"])


def test_cli_plot(tmp_path: Path):
    pytest.importorskip("matplotlib")
    make_session(tmp_path)
    assert main([str(tmp_path), "--dry-run", "--plot"]) == 0
    assert (tmp_path / "selection_plot.png").stat().st_size > 0


def _synthetic_frames(n: int = 20):
    from astro_light_selector.metrics import FrameMetrics
    # FWHM 從 3.0 線性變差到 5.0，分數會跟著單調下降
    return [FrameMetrics(f"f{i:02d}", 200, 3.0 + 2.0 * i / (n - 1), 0.3, 800, 10, 20, 0, 120.0, "L", None)
            for i in range(n)]


def test_min_score_overrides_auto_threshold():
    from astro_light_selector.scoring import ScoreConfig
    from astro_light_selector.selector import select_by_score

    decisions, result = select_by_score(_synthetic_frames(), ScoreConfig(min_score=90))
    assert result.method == "min_score"
    assert result.threshold == 90
    for d in decisions:
        assert d.keep == (d.score >= 90)


def test_keep_best_keeps_top_fraction():
    from astro_light_selector.scoring import ScoreConfig
    from astro_light_selector.selector import select_by_score

    frames = _synthetic_frames(20)
    decisions, result = select_by_score(frames, ScoreConfig(keep_best=0.7))
    assert result.method == "keep_best"
    kept = [d.metrics.file for d in decisions if d.keep]
    # FWHM 最小的 14 張
    assert kept == [f"f{i:02d}" for i in range(14)]


def test_keep_best_ignores_unmeasurable_frames():
    from astro_light_selector.metrics import FrameMetrics
    from astro_light_selector.scoring import ScoreConfig
    from astro_light_selector.selector import select_by_score

    frames = _synthetic_frames(10) + [
        FrameMetrics("cloud", 0, float("nan"), float("nan"), 3000, 10, 0, 0, 120.0, "L", None, error="偵測不到星點")
    ]
    decisions, _ = select_by_score(frames, ScoreConfig(keep_best=0.5))
    assert sum(d.keep for d in decisions) == 5


@pytest.mark.parametrize("extra", [
    ["--min-score", "85", "--keep-best", "0.7"],   # 兩個不能一起用
    ["--mode", "rules", "--min-score", "85"],      # 只能用在評分模式
    ["--keep-best", "70"],                         # 比例不是百分比
    ["--min-score", "150"],
])
def test_cli_rejects_bad_direct_threshold(tmp_path: Path, extra: list[str]):
    make_session(tmp_path, n_good=3)
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--dry-run", *extra])


def test_cli_keep_best(tmp_path: Path):
    make_session(tmp_path, n_good=9)  # 共 12 張
    assert main([str(tmp_path), "--dry-run", "--keep-best", "0.5"]) == 0
    report = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert sum(line.split(",")[1] == "keep" for line in report.splitlines()[1:]) == 6


def _assert_locations_match_report(folder: Path) -> None:
    """報表上 keep 的在原位，reject 的不在原位。"""
    import csv
    with (folder / "selection_report.csv").open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            assert (folder / row["file"]).exists() == (row["keep"] == "keep"), row["file"]


def test_rerun_from_report_moves_back_frames_now_keep(tmp_path: Path):
    make_session(tmp_path)
    report = tmp_path / "selection_report.csv"
    seeing = tmp_path / "bad_seeing.fits"
    assert main([str(tmp_path)]) == 0
    assert not seeing.exists()
    _assert_locations_match_report(tmp_path)

    # dry-run 只預告，不搬回
    assert main([str(tmp_path), "--from-report", str(report), "--min-score", "0", "--dry-run"]) == 0
    assert not seeing.exists()

    # 放寬到全部 keep：之前 reject、量得出來的要搬回原位
    assert main([str(tmp_path), "--from-report", str(report), "--min-score", "0"]) == 0
    assert seeing.exists()
    _assert_locations_match_report(tmp_path)

    # 再收緊：又搬出去
    assert main([str(tmp_path), "--from-report", str(report), "--min-score", "100"]) == 0
    _assert_locations_match_report(tmp_path)


def test_rerun_measure_includes_previous_rejects(tmp_path: Path):
    files = make_session(tmp_path)
    rejected = tmp_path / "rejected"
    assert main([str(tmp_path)]) == 0
    first = sorted(p.name for p in rejected.glob("*.fits"))

    # 重新量測時整批都要算進來，結果跟第一次一樣，不會越挑越嚴
    assert main([str(tmp_path)]) == 0
    assert sorted(p.name for p in rejected.glob("*.fits")) == first
    report = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert len(report.splitlines()) - 1 == len(files["good"]) + len(files["bad"])

    # 放寬後重新量測，之前 reject 的也會搬回來
    assert main([str(tmp_path), "--min-score", "0"]) == 0
    assert (tmp_path / "bad_seeing.fits").exists()
    _assert_locations_match_report(tmp_path)


@pytest.mark.parametrize("old_home", [r"C:\Users\someone\Desktop\NGC7635\Light", "/Users/someone/NGC7635/Light"])
def test_move_log_follows_moved_folder(tmp_path: Path, old_home: str):
    """在別台電腦挑過、整個資料夾複製過來：紀錄裡的絕對路徑對不上，要照 rejected 現在的位置換算。"""
    files = make_session(tmp_path)
    rejected = tmp_path / "rejected"
    assert main([str(tmp_path)]) == 0
    first = sorted(p.name for p in rejected.glob("*.fits"))
    assert first

    sep = "\\" if "\\" in old_home else "/"
    log = rejected / "moves.csv"
    log.write_text("source,target\n" + "".join(f"{old_home}{sep}{n},{old_home}{sep}rejected{sep}{n}\n" for n in first),
                   encoding="utf-8-sig")

    # 重新量測：之前 reject 的也算進同一批，結果不變
    assert main([str(tmp_path)]) == 0
    assert sorted(p.name for p in rejected.glob("*.fits")) == first
    report = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert len(report.splitlines()) - 1 == len(files["good"]) + len(files["bad"])

    # 放寬後照紀錄搬回原位
    assert main([str(tmp_path), "--min-score", "0"]) == 0
    assert (tmp_path / "bad_seeing.fits").exists()
    _assert_locations_match_report(tmp_path)


def test_rerun_leaves_manual_rejects_alone(tmp_path: Path):
    make_session(tmp_path)
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    # 使用者自己丟進 rejected 的檔案，其中一張跟 bad_seeing 同名
    same_name = write_fits(rejected / "bad_seeing.fits", make_star_field(seed=7))
    manual = write_fits(rejected / "manual.fits", make_star_field(seed=8))

    assert main([str(tmp_path)]) == 0
    assert (rejected / "bad_seeing_1.fits").exists()  # 同名不覆蓋
    assert "manual.fits" not in (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")

    report = tmp_path / "selection_report.csv"
    assert main([str(tmp_path), "--from-report", str(report), "--min-score", "0"]) == 0
    assert (tmp_path / "bad_seeing.fits").exists()  # 從 bad_seeing_1 搬回原名
    assert same_name.exists() and manual.exists()   # 手動丟的沒被動
    assert not (tmp_path / "manual.fits").exists()


def test_reasons_and_errors_follow_language():
    from astro_light_selector import i18n
    from astro_light_selector.metrics import FrameMetrics
    from astro_light_selector.scoring import ScoreConfig
    from astro_light_selector.selector import select_by_score

    frames = _synthetic_frames(10) + [
        FrameMetrics("cloud", 0, float("nan"), float("nan"), 3000, 10, 0, 0, 120.0, "L", None,
                     error="few_fit_stars:5")
    ]
    decisions, _ = select_by_score(frames, ScoreConfig(min_score=90))
    low = next(d for d in decisions if not d.keep and d.score is not None)
    cloud = decisions[-1]
    try:
        assert str(low.reasons[0]).startswith("分數 ") and "弱項：FWHM" in str(low.reasons[0])
        assert str(cloud.reasons[0]) == "只有 5 顆星能擬合，可能被雲遮住或只剩熱像素"
        i18n.set_language("en")
        assert str(low.reasons[0]).startswith("Score ") and "weakest: FWHM" in str(low.reasons[0])
        assert str(cloud.reasons[0]).startswith("Only 5 stars could be fitted")
    finally:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)


def test_old_report_error_text_is_understood(tmp_path: Path):
    from astro_light_selector.report import read_csv

    report = tmp_path / "old.csv"
    report.write_text(
        "file,keep,score,reasons,n_stars,fwhm,eccentricity,background,noise,snr,saturated_frac,"
        "exposure,filter,date_obs,error\n"
        "a.fit,reject,,,671,,,1500,20,5,0,300.0,,2026-09-06T01:25:27,"
        "只有 5 顆星能擬合，可能被雲遮住或只剩熱像素\n", encoding="utf-8-sig")
    old = read_csv(report, tmp_path)[0]
    assert old.error == "few_fit_stars:5"
    assert old.altitude is None  # 舊報表沒有拍攝資訊欄位


def test_exposure_tolerance_merges_bulb_timing():
    from astro_light_selector.grouping import exposure_buckets, group_frames
    from astro_light_selector.metrics import FrameMetrics

    def fm(name: str, exp: float) -> FrameMetrics:
        return FrameMetrics(name, 100, 3.0, 0.2, 800, 10, 20, 0, exp, None, None)

    frames = [fm("a", 301.0), fm("b", 302.0), fm("c", 301.0), fm("d", 60.0), fm("e", 120.0)]
    assert exposure_buckets(frames, 2) == {60.0: "60s", 120.0: "120s", 301.0: "301~302s", 302.0: "301~302s"}
    groups = group_frames(frames, ("exposure",), 2)
    assert sorted(len(v) for v in groups.values()) == [1, 1, 3]
    # 誤差設 0：301 和 302 分開
    assert len(group_frames(frames, ("exposure",), 0)) == 4


def test_raw_bayer_and_xtrans_are_measured(monkeypatch, tmp_path: Path):
    """用合成星場假裝成相機 RAW（2×2 Bayer 和 6×6 X-Trans），確認整個量測流程走得通。"""
    import datetime
    from types import SimpleNamespace

    import numpy as np
    import rawpy

    field = make_star_field(shape=(600, 600), fwhm=4.0, n_stars=200, background=600, seed=3).astype(np.float32)

    def fake_raw(pattern: np.ndarray, desc: bytes):
        tiles = (field.shape[0] // pattern.shape[0], field.shape[1] // pattern.shape[1])
        colors = np.tile(pattern, tiles)
        raw = SimpleNamespace(
            raw_image_visible=(field[:colors.shape[0], :colors.shape[1]] + 512).astype(np.uint16),
            raw_colors_visible=colors, raw_pattern=pattern, color_desc=desc,
            black_level_per_channel=[512, 512, 512, 512], white_level=16383,
            other=SimpleNamespace(shutter_speed=300.0, iso_speed=800.0,
                                  timestamp=datetime.datetime(2026, 9, 5, 21, 0, 0)),
        )
        raw.__enter__ = lambda self=raw: raw
        return raw

    class Ctx:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            return self.raw

        def __exit__(self, *exc):
            return False

    bayer = np.array([[0, 1], [3, 2]])
    xtrans = np.array([[1, 1, 0, 1, 1, 2], [1, 1, 2, 1, 1, 0], [2, 0, 1, 0, 2, 1],
                       [1, 1, 2, 1, 1, 0], [1, 1, 0, 1, 1, 2], [0, 2, 1, 2, 0, 1]])
    for name, pattern, desc in (("a.CR2", bayer, b"RGBG"), ("b.RAF", xtrans, b"RGBG")):
        monkeypatch.setattr(rawpy, "imread", lambda _p, r=fake_raw(pattern, desc): Ctx(r))
        path = tmp_path / name
        path.write_bytes(b"x")
        m = measure(path)
        assert m.error is None, m.error
        assert 3.2 <= m.fwhm <= 4.8, m.fwhm
        assert m.exposure == 300.0 and m.date_obs == "2026-09-05T21:00:00"


def test_cli_respects_manual_overrides(tmp_path: Path):
    import csv

    files = make_session(tmp_path)
    assert main([str(tmp_path), "--dry-run"]) == 0
    report = tmp_path / "selection_report.csv"
    rows = list(csv.DictReader(report.open(encoding="utf-8-sig", newline="")))
    bad = files["bad"][0].name
    for r in rows:
        if r["file"] == bad:
            r["override"] = "keep"
    with report.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # 視窗版標成手動保留的，命令列重跑（不論重新量測或讀報表）也要照樣保留
    for extra in (["--from-report", str(report)], []):
        assert main([str(tmp_path), "--dry-run", *extra]) == 0
        again = {r["file"]: r for r in csv.DictReader(report.open(encoding="utf-8-sig", newline=""))}
        assert again[bad]["keep"] == "keep" and again[bad]["override"] == "keep"


def test_sky_info_from_header():
    from astropy.io import fits

    from astro_light_selector.sky import sky_info

    def header(**cards):
        h = fits.Header()
        h["SITELAT"], h["SITELONG"], h["EXPTIME"] = 23.5, 120.5, 300.0
        for k, v in cards.items():
            h[k] = v
        return h

    # 北天極的仰角 = 當地緯度；2024-09-18 是滿月、2024-10-02 是新月
    full = sky_info(header(RA=0.0, DEC=89.9999, **{"DATE-OBS": "2024-09-18T02:34:00"}))
    assert abs(full.altitude - 23.5) < 0.3
    assert full.moon_illum > 0.97
    assert 0 <= full.moon_sep <= 180
    # 六十進位字串（OBJCTRA 是時角）也讀得懂
    new = sky_info(header(OBJCTRA="00 00 00", OBJCTDEC="+89 59 59", **{"DATE-OBS": "2024-10-02T18:49:00"}))
    assert abs(new.altitude - 23.5) < 0.3
    assert new.moon_illum < 0.03
    # 缺地點（例如相機 RAW）就不算
    assert sky_info(fits.Header({"RA": 10.0, "DEC": 20.0, "DATE-OBS": "2024-10-02T18:49:00"})) is None


def test_sky_info_is_measured_saved_and_plotted(tmp_path: Path):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    from astro_light_selector.plot import draw_decisions
    from astro_light_selector.report import read_csv, write_csv

    site = dict(sitelat=23.5, sitelong=120.5, ra=0.0, dec=89.9999)
    paths = [write_fits(tmp_path / f"s{i}.fits", make_star_field(fwhm=3.0, n_stars=150, seed=i),
                        date_obs=f"2024-09-18T1{i}:00:00", **site) for i in range(3)]
    frames = [measure(p) for p in paths]
    assert all(abs(m.altitude - 23.5) < 0.3 and m.moon_illum is not None for m in frames)

    decisions = select(frames, Thresholds())
    report = tmp_path / "report.csv"
    write_csv(decisions, report)
    again = read_csv(report, tmp_path)
    assert [m.altitude for m in again] == pytest.approx([m.altitude for m in frames], abs=1e-3)

    fig = Figure()
    draw_decisions(fig, decisions, dark=True)
    assert any(ax.get_ylabel().startswith(("仰角", "Altitude")) for ax in fig.axes)


def _multi_night(root: Path) -> dict[str, dict[str, list[Path]]]:
    """單眼常見的放法：light/iso800/每晚一個資料夾，檔名每晚重複；旁邊還有一個不該被掃進來的暗場資料夾。"""
    nights = {name: make_session(root / "iso800" / name, n_good=6) for name in ("date_2026 0101", "date_2026 0202")}
    (root / "iso800" / "darks").mkdir()
    write_fits(root / "iso800" / "darks" / "dark_001.fits", make_star_field(n_stars=0, seed=7))
    return nights


def test_recursive_rejects_into_each_night_folder(tmp_path: Path):
    from astro_light_selector.pipeline import should_recurse
    from astro_light_selector.report import read_csv, read_overrides

    nights = _multi_night(tmp_path)
    assert should_recurse(tmp_path)
    # 開的是最上層：本身沒有影像，自動包含子資料夾
    assert main([str(tmp_path), "--workers", "2"]) == 0
    for name, files in nights.items():
        night = tmp_path / "iso800" / name
        assert sorted(p.name for p in (night / "rejected").glob("*.fits")) == sorted(p.name for p in files["bad"])
        assert all(p.exists() for p in files["good"])
    assert (tmp_path / "iso800" / "darks" / "dark_001.fits").exists()  # 暗場資料夾沒被掃進來
    assert not (tmp_path / "rejected").exists()

    # 報表在最上層，用相對路徑分得開同名檔案
    report = tmp_path / "selection_report.csv"
    keys = {Path(f.file).relative_to(tmp_path).as_posix() for f in read_csv(report, tmp_path)}
    assert "iso800/date_2026 0101/light_000.fits" in keys and "iso800/date_2026 0202/light_000.fits" in keys
    assert len(keys) == 18

    # 重跑：之前搬走的也算進同一批；手動覆寫用相對路徑認片
    text = report.read_text(encoding="utf-8-sig").replace(
        "iso800/date_2026 0202/bad_seeing.fits,reject", "iso800/date_2026 0202/bad_seeing.fits,keep")
    lines = text.splitlines()
    header = lines[0].split(",")
    i_override, i_file = header.index("override"), header.index("file")
    for n, line in enumerate(lines[1:], 1):
        cells = line.split(",")
        if cells[i_file] == "iso800/date_2026 0202/bad_seeing.fits":
            cells[i_override] = "keep"
            lines[n] = ",".join(cells)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    assert read_overrides(report) == {"iso800/date_2026 0202/bad_seeing.fits": "keep"}
    assert main([str(tmp_path), "--from-report", str(report)]) == 0
    assert (tmp_path / "iso800" / "date_2026 0202" / "bad_seeing.fits").exists()      # 覆寫成保留，搬回來
    assert (tmp_path / "iso800" / "date_2026 0101" / "rejected" / "bad_seeing.fits").exists()  # 另一晚的照舊

    assert main([str(tmp_path), "--restore"]) == 0
    for files in nights.values():
        assert all(p.exists() for p in files["good"] + files["bad"])


def test_recursive_custom_reject_dir_keeps_structure(tmp_path: Path):
    nights = _multi_night(tmp_path)
    # 自訂的淘汰片資料夾放在開啟的資料夾裡面：要照子資料夾結構放，而且重跑時不能被當成影像資料夾掃進來
    elsewhere = tmp_path / "second pass"
    assert main([str(tmp_path), "--reject-dir", str(elsewhere), "--workers", "2"]) == 0
    for name, files in nights.items():
        moved = elsewhere / "iso800" / name
        assert sorted(p.name for p in moved.glob("*.fits")) == sorted(p.name for p in files["bad"])
        assert not (tmp_path / "iso800" / name / "rejected").exists()

    # 讀上次的報表重跑：搬走的片照樣算進同一批，結果不變、不會再搬
    assert main([str(tmp_path), "--reject-dir", str(elsewhere), "--from-report",
                 str(tmp_path / "selection_report.csv")]) == 0
    text = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert text.count("\nsecond pass/") == 0 and text.count(",reject,") == 6

    # 全部還原：搬回各晚的資料夾，照結構建的空資料夾一起收掉
    assert main([str(tmp_path), "--reject-dir", str(elsewhere), "--restore"]) == 0
    assert all(p.exists() for files in nights.values() for p in files["bad"])
    assert not elsewhere.exists()


def test_report_paths_matched_by_file_name(tmp_path: Path):
    """報表是子資料夾模式存的，後來檔案被攤平到同一層：用檔名認回，不用重新量測。"""
    import shutil

    from astro_light_selector.pipeline import collect_files, should_recurse
    from astro_light_selector.report import match_frames, read_csv, report_subfolders

    a = make_session(tmp_path / "night1", n_good=3)
    for p in make_session(tmp_path / "night2", n_good=3)["good"]:
        p.rename(p.with_name(f"n2_{p.name}"))
    for p in (tmp_path / "night2").glob("bad_*.fits"):
        p.rename(p.with_name(f"n2_{p.name}"))
    assert main([str(tmp_path), "--dry-run"]) == 0
    report = tmp_path / "selection_report.csv"
    assert report_subfolders(report, tmp_path)
    for night in ("night1", "night2"):
        for p in (tmp_path / night).glob("*.fits"):
            shutil.move(str(p), str(tmp_path / p.name))
        shutil.rmtree(tmp_path / night)
    assert not report_subfolders(report, tmp_path) and not should_recurse(tmp_path)

    frames = read_csv(report, tmp_path)
    files, home_of = collect_files(tmp_path)
    kept, renamed = match_frames(frames, files, home_of, tmp_path)
    assert len(kept) == len(files) == len(frames)
    assert renamed[f"night1/{a['good'][0].name}"] == a["good"][0].name
    assert all(Path(f.file).parent == tmp_path for f in kept)


def test_report_match_skips_duplicate_names(tmp_path: Path):
    """單眼的檔名每晚重複：同名的有好幾個就不猜，當成新檔案。"""
    from astro_light_selector.metrics import FrameMetrics
    from astro_light_selector.report import match_frames

    def frame(rel):
        return FrameMetrics(str(tmp_path / rel), 100, 3.0, 0.2, 800, 10, 20, 0, 120.0, None, None)

    frames = [frame("a/DSC1.ARW"), frame("b/DSC1.ARW"), frame("a/DSC2.ARW")]
    files = [tmp_path / "x" / "DSC1.ARW", tmp_path / "y" / "DSC1.ARW", tmp_path / "x" / "DSC2.ARW"]
    kept, renamed = match_frames(frames, files, {}, tmp_path)
    assert renamed == {"a/DSC2.ARW": "x/DSC2.ARW"}
    assert [Path(f.file).name for f in kept] == ["DSC2.ARW"]


def test_organize_and_undo(tmp_path: Path):
    from astro_light_selector.metrics import FrameMetrics
    from astro_light_selector.organize import ORGANIZE_LOG, organize, plan_organize, undo_organize

    def frame(name, camera, filt, exposure, date):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
        return FrameMetrics(str(tmp_path / name), 100, 3.0, 0.2, 800, 10, 20, 0, exposure, filt, date,
                            gain=100.0, camera=camera)

    frames = [frame("a.fits", "ZWO ASI2600MM Pro", "Ha", 300.0, "2026-01-01T12:00:00"),
              frame("b.fits", "ZWO ASI2600MM Pro", "OIII", 300.0, "2026-01-01T13:00:00"),
              frame("c.fits", None, None, 301.0, "2026-01-01T14:00:00"),
              frame("night2/a.fits", "ZWO ASI2600MM Pro", "Ha", 300.0, "2026-01-02T12:00:00")]
    plan = plan_organize(frames, tmp_path, ("camera", "filter", "exposure"))
    targets = {cur.relative_to(tmp_path.resolve()).as_posix(): t.relative_to(tmp_path.resolve()).as_posix()
               for cur, t in plan}
    assert targets == {
        "a.fits": "ZWO ASI2600MM Pro/Ha/300~301s/a.fits",
        "b.fits": "ZWO ASI2600MM Pro/OIII/300~301s/b.fits",
        "c.fits": "UnknownCamera/NoFilter/300~301s/c.fits",
        # 原本的子資料夾（每晚一個）保留那一層，同名的檔案才不會撞在一起
        "night2/a.fits": "ZWO ASI2600MM Pro/Ha/300~301s/night2/a.fits",
    }
    done = organize(plan, tmp_path)
    assert len(done) == 4 and (tmp_path / ORGANIZE_LOG).is_file()
    assert not (tmp_path / "night2").exists()  # 搬空的資料夾收掉
    # 已經整理好的再整理一次：從原位置算，不會越疊越深
    for f, (_, target) in zip(frames, done):
        f.file = str(target)
    assert plan_organize(frames, tmp_path, ("camera", "filter", "exposure")) == []

    back = undo_organize(tmp_path)
    assert len(back) == 4
    assert all((tmp_path / n).is_file() for n in ("a.fits", "b.fits", "c.fits", "night2/a.fits"))
    assert not (tmp_path / ORGANIZE_LOG).exists() and not (tmp_path / "ZWO ASI2600MM Pro").exists()


def test_organize_folder_names_are_safe():
    from astro_light_selector.organize import safe_name

    assert safe_name('L/R:G*B?') == "L_R_G_B_"
    assert safe_name(" .hidden. ") == "hidden"
    assert safe_name("") == "_"


def test_kept_exposure_and_duration(tmp_path: Path):
    from astro_light_selector.metrics import FrameMetrics
    from astro_light_selector.report import format_duration, kept_exposure
    from astro_light_selector.selector import Decision

    def d(keep, exposure):
        m = FrameMetrics(str(tmp_path / "x.fits"), 100, 3.0, 0.2, 800, 10, 20, 0, exposure, None, None)
        return Decision(m, keep, [])

    assert kept_exposure([d(True, 300.0)] * 203 + [d(False, 300.0)] * 93 + [d(True, None)]) == (60900.0, 204, 1)
    assert format_duration(60900) == "16h 55m"
    assert format_duration(45 * 60) == "45m"
    assert format_duration(30) == "30s"


def test_parse_number():
    from astro_light_selector.darkroom import parse_number

    assert parse_number("85%") == 85
    assert parse_number("８５ 分") == 85
    assert parse_number("2.5 秒") == 2.5
    assert parse_number("abc") is None


def test_single_folder_report_keeps_plain_file_names(tmp_path: Path):
    make_session(tmp_path, n_good=3)
    assert main([str(tmp_path), "--dry-run"]) == 0
    text = (tmp_path / "selection_report.csv").read_text(encoding="utf-8-sig")
    assert "\nlight_000.fits," in text and "/" not in text.split("\n")[1].split(",")[0]


def test_group_by_gain_and_folder(tmp_path: Path):
    from astro_light_selector.grouping import group_frames
    from astro_light_selector.metrics import FrameMetrics

    def frame(name, iso=None, gain=None):
        return FrameMetrics(str(tmp_path / name), 100, 3.0, 0.2, 800, 10, 20, 0, 120.0, "L", None, iso=iso, gain=gain)

    groups = group_frames([frame("a/1.ARW", iso=800), frame("a/2.ARW", iso=1600), frame("b/3.ARW", iso=800)],
                          ("filter", "exposure", "gain"))
    assert sorted(len(v) for v in groups.values()) == [1, 2]
    assert any("ISO 800" in label for label in groups)
    # 增益（天文相機）一樣分開
    assert len(group_frames([frame("1.fits", gain=100), frame("2.fits", gain=200)], ("gain",))) == 2
    # 整批都沒有 ISO / 增益（例如舊報表）：不分、標籤也不加那一段
    groups = group_frames([frame("1.fits"), frame("2.fits")], ("filter", "gain"))
    assert list(groups) == ["濾鏡 L"]
    # 依子資料夾分開
    groups = group_frames([frame("a/1.ARW", 800), frame("a/2.ARW", 800), frame("b/1.ARW", 800)],
                          ("folder",), root=tmp_path)
    assert sorted(groups) == ["資料夾 a", "資料夾 b"]


def test_night_split_needs_eight_hour_gap():
    from astro_light_selector.grouping import assign_nights
    from astro_light_selector.metrics import FrameMetrics

    def frame(name, t):
        return FrameMetrics(name, 100, 3.0, 0.2, 800, 10, 20, 0, 120.0, "L", t)

    nights = assign_nights([
        frame("a", "2026-01-01T20:00:00"), frame("b", "2026-01-01T23:30:00"),
        frame("c", "2026-01-02T05:00:00"),   # 雲擋了 5.5 小時、後半夜再拍：同一晚，跨過午夜也一樣
        frame("d", "2026-01-02T19:00:00"),   # 隔了 14 小時：下一晚
    ])
    assert nights["a"] == nights["b"] == nights["c"] == "2026-01-01"
    assert nights["d"] == "2026-01-02"


def test_raf_camera_from_header(tmp_path: Path):
    from astro_light_selector.rawfile import _camera

    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"FUJIFILMCCD-RAW 0201FF179504" + b"X-E5".ljust(32, b"\0") + b"\0" * 100)
    assert _camera(raf) == "FUJIFILM X-E5"
    other = tmp_path / "x.RAF"
    other.write_bytes(b"not a raf")
    assert _camera(other) is None


def test_cr3_camera_from_cmt1(tmp_path: Path):
    """Canon CR3：型號在 moov → Canon uuid → CMT1（一小段 TIFF）裡。"""
    import struct

    from astro_light_selector.rawfile import _CANON_UUID, _camera

    def box(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I4s", 8 + len(body), kind) + body

    make, model = b"Canon\0", b"Canon EOS R6\0"
    data_at = 8 + 2 + 2 * 12 + 4
    tiff = (b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", 2)
            + struct.pack("<HHII", 0x010F, 2, len(make), data_at)
            + struct.pack("<HHII", 0x0110, 2, len(model), data_at + len(make))
            + struct.pack("<I", 0) + make + model)
    # CMT2 是 EXIF：曝光 300/1 秒、ISO 800、拍攝時間（Intel Mac 的舊版 rawpy 讀不到時靠它）
    stamp = b"2026:01:02 21:30:00\0"
    exif = (b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", 3)
            + struct.pack("<HHII", 0x829A, 5, 1, 50)
            + struct.pack("<HHI", 0x8827, 3, 1) + struct.pack("<HH", 800, 0)
            + struct.pack("<HHII", 0x9003, 2, len(stamp), 58)
            + struct.pack("<I", 0) + struct.pack("<II", 300, 1) + stamp)
    cr3 = tmp_path / "IMG_0001.CR3"
    cr3.write_bytes(box(b"ftyp", b"crx \0\0\0\1")
                    + box(b"moov", box(b"uuid", _CANON_UUID + box(b"CNCV", b"x") + box(b"CMT1", tiff)
                                       + box(b"CMT2", exif))))
    assert _camera(cr3) == "Canon EOS R6"

    from datetime import datetime

    from astro_light_selector.rawfile import _metadata

    class OldRawpy:  # 舊版 rawpy 沒有 raw.other
        pass

    assert _metadata(OldRawpy(), cr3) == (300.0, 800.0, datetime(2026, 1, 2, 21, 30))
