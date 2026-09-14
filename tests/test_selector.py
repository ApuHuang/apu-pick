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
    assert sorted(p.name for p in rejected.iterdir()) == sorted(p.name for p in files["bad"])
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
