"""The rehearsal fixture for the admission layout says what it means to say.

The fixture runs on the worker box only (its datasets exist there), so nothing here runs
it. These tests hold the parts a typo would break without a word: the hook ignores a
research option it does not know, so a misspelt ``window_layout`` would run the 90-day
layout and report success.
"""

from __future__ import annotations

from pathlib import Path

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
FIXTURE = (
    REPO_ROOT / "fixtures" / "platform" / "replays" / "medium" / "research_admission_rehearsal.yaml"
)
# Every key run_scheduled_research_at_timestamp reads from research.options.
OPTIONS_THE_HOOK_READS = {
    "profile",
    "lookback_days",
    "universe_timeline",
    "dataset_version",
    "window_layout",
}


def _research_options() -> dict:
    fixture = load_fixture(FIXTURE)
    assert _job_enabled(fixture.scheduled_jobs, "research")
    return _job_options(fixture.scheduled_jobs, "research")


def test_the_fixture_loads_and_asks_for_the_admission_layout() -> None:
    options = _research_options()

    assert set(options) <= OPTIONS_THE_HOOK_READS
    assert rh._resolve_window_layout_option(options) == rh.WINDOW_LAYOUT_ADMISSION
    # The layout is refused without a dataset and a timeline, and with a window length.
    assert rh._resolve_dataset_version_option(options) is not None
    assert rh._resolve_universe_timeline_option(options) is not None
    assert "lookback_days" not in options
    assert rh._resolve_research_profile(options) == "smoke"


def test_the_options_checked_here_are_the_ones_the_hook_documents() -> None:
    """If the hook learns a new option, this list is looked at again."""
    documented = rh.run_scheduled_research_at_timestamp.__doc__ or ""
    for key in OPTIONS_THE_HOOK_READS:
        assert f"      {key}:" in documented, key


def test_research_fires_on_a_session_the_layout_accepts() -> None:
    """Research is monthly, so it fires on the replay's first tick: a trading day on or
    after the holdout date, or the layout refuses the run."""
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    start, end = raw["platform_replay"]["start"], raw["platform_replay"]["end"]

    assert raw["scheduled_jobs"]["research"]["cadence"] == "monthly"
    assert start >= HOLDOUT_START
    assert list(RealMarketCalendar().trading_days(start, start)) == [start]
    assert start < end
