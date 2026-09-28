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
    assert sorted(groups) == ["filter=Ha", "filter=L"]
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
