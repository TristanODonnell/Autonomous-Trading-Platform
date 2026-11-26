"""The worker-box fixtures for the admission layout say what they mean to say.

Two fixtures: the rehearsal (smoke profile) and the baseline (full profile). They run on
the worker box only (their datasets exist there), so nothing here runs them. These tests
hold the parts a typo would break without a word: the hook ignores a research option it
does not know, so a misspelt ``window_layout`` would run the 90-day layout and report
success.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
from autonomous_trading_platform.application.services.platform_backtest_service import (
    _job_enabled,
    _job_options,
)
from autonomous_trading_platform.platform.replay.platform_replay_config import load_fixture
from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START
from autonomous_trading_platform.runtime.clock import RealMarketCalendar

REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIUM = REPO_ROOT / "fixtures" / "platform" / "replays" / "medium"
REHEARSAL = MEDIUM / "research_admission_rehearsal.yaml"
BASELINE = MEDIUM / "research_admission_baseline.yaml"
CONTROLS = MEDIUM / "research_planted_controls.yaml"
NOISE40 = MEDIUM / "research_planted_controls_noise40.yaml"
CHECK40 = MEDIUM / "research_planted_controls_check40.yaml"
# Fixture file and the research profile it is meant to run.
FIXTURES = [
    pytest.param(REHEARSAL, "smoke", id="rehearsal"),
    pytest.param(BASELINE, "full", id="baseline"),
    pytest.param(CONTROLS, "full", id="planted_controls"),
    pytest.param(NOISE40, "full", id="planted_controls_noise40"),
    pytest.param(CHECK40, "full", id="planted_controls_check40"),
]
# Every key run_scheduled_research_at_timestamp reads from research.options.
OPTIONS_THE_HOOK_READS = {
    "profile",
    "lookback_days",
    "universe_timeline",
    "dataset_version",
    "window_layout",
    "candidates",
    "noise_seeds",
    "noise_first_seed",
}


def _research_options(path: Path) -> dict:
    fixture = load_fixture(path)
    assert _job_enabled(fixture.scheduled_jobs, "research")
    return _job_options(fixture.scheduled_jobs, "research")


@pytest.mark.parametrize(("path", "profile"), FIXTURES)
def test_the_fixture_loads_and_asks_for_the_admission_layout(path: Path, profile: str) -> None:
    options = _research_options(path)

    assert set(options) <= OPTIONS_THE_HOOK_READS
    assert rh._resolve_window_layout_option(options) == rh.WINDOW_LAYOUT_ADMISSION
    # The layout is refused without a dataset and a timeline, and with a window length.
    assert rh._resolve_dataset_version_option(options) is not None
    assert rh._resolve_universe_timeline_option(options) is not None
    assert "lookback_days" not in options
    # Named in the file, not left to the default: the two fixtures differ in this alone.
    assert options["profile"] == profile
    assert rh._resolve_research_profile(options) == profile


def test_the_options_checked_here_are_the_ones_the_hook_documents() -> None:
    """If the hook learns a new option, this list is looked at again."""
    documented = rh.run_scheduled_research_at_timestamp.__doc__ or ""
    for key in OPTIONS_THE_HOOK_READS:
        assert f"      {key}:" in documented, key


@pytest.mark.parametrize(("path", "profile"), FIXTURES)
def test_research_fires_on_a_session_the_layout_accepts(path: Path, profile: str) -> None:
    """Research is monthly, so it fires on the replay's first tick: a trading day on or
    after the holdout date, or the layout refuses the run."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    start, end = raw["platform_replay"]["start"], raw["platform_replay"]["end"]

    assert raw["scheduled_jobs"]["research"]["cadence"] == "monthly"
    assert start >= HOLDOUT_START
    assert list(RealMarketCalendar().trading_days(start, start)) == [start]
    assert start < end


def test_the_two_fixtures_differ_in_name_and_profile_alone() -> None:
    """The baseline is read against the rehearsal, so nothing else may drift apart."""
    rehearsal = yaml.safe_load(REHEARSAL.read_text(encoding="utf-8"))
    baseline = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))

    assert rehearsal["platform_replay"].pop("name") == "research_admission_rehearsal"
    assert baseline["platform_replay"].pop("name") == "research_admission_baseline"
    assert rehearsal["scheduled_jobs"]["research"]["options"].pop("profile") == "smoke"
    assert baseline["scheduled_jobs"]["research"]["options"].pop("profile") == "full"
    assert rehearsal == baseline


def test_the_controls_fixture_is_the_baseline_with_the_controls_in_place_of_the_draw() -> None:
    """The controls are read against the 2026-11-25 run: same dataset, timeline, dates
    and profile, so the name and the candidates option are all that may differ."""
    baseline = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))
    controls = yaml.safe_load(CONTROLS.read_text(encoding="utf-8"))

    assert rh._resolve_candidates_option(_research_options(BASELINE)) == rh.CANDIDATES_DRAW
    assert (
        rh._resolve_candidates_option(_research_options(CONTROLS)) == rh.CANDIDATES_PLANTED_CONTROLS
    )
    assert baseline["platform_replay"].pop("name") == "research_admission_baseline"
    assert controls["platform_replay"].pop("name") == "research_planted_controls"
    assert controls["scheduled_jobs"]["research"]["options"].pop("candidates") == (
        "planted_controls"
    )
    assert controls == baseline


def test_the_noise_sample_fixture_is_the_controls_fixture_with_40_noise_seeds() -> None:
    """The noise sample is read against the first controls run, so the name and the
    sample size are all that may differ."""
    controls = yaml.safe_load(CONTROLS.read_text(encoding="utf-8"))
    noise = yaml.safe_load(NOISE40.read_text(encoding="utf-8"))

    options = _research_options(NOISE40)
    sample = rh._resolve_planted_controls(options, rh._resolve_candidates_option(options))
    assert sample is not None and len(sample) == 43
    assert controls["platform_replay"].pop("name") == "research_planted_controls"
    assert noise["platform_replay"].pop("name") == "research_planted_controls_noise40"
    assert noise["scheduled_jobs"]["research"]["options"].pop("noise_seeds") == 40
    assert noise == controls


def test_the_check_fixture_is_the_noise_sample_on_fresh_seeds() -> None:
    """The tightened gates are checked on 40 noise controls none of the earlier runs used
    (seeds 141 to 180): the name and the first seed are all that may differ."""
    noise = yaml.safe_load(NOISE40.read_text(encoding="utf-8"))
    check = yaml.safe_load(CHECK40.read_text(encoding="utf-8"))

    options = _research_options(CHECK40)
    sample = rh._resolve_planted_controls(options, rh._resolve_candidates_option(options))
    assert sample is not None
    assert [c.seed for c in sample[3:]] == list(range(141, 181))
    assert noise["platform_replay"].pop("name") == "research_planted_controls_noise40"
    assert check["platform_replay"].pop("name") == "research_planted_controls_check40"
    assert check["scheduled_jobs"]["research"]["options"].pop("noise_first_seed") == 141
    assert check == noise
