"""A walk-forward test fold spans at least twelve weeks (research plan R2).

How well a Sharpe ratio is known depends on the calendar span it was measured over. Over
ten sessions its standard error is about 5 a year, so a short fold's pass or fail is close
to a coin toss. The admission layout already refuses a fold under 60 sessions; the
walk-forward config held no minimum, so rolling folds and hand-written ones could be any
length. A config that keeps short folds now says so (``allow_short_test_folds``).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
from autonomous_trading_platform.research.experiments.filtering.config import ScoringWeights
from autonomous_trading_platform.research.pipeline.admission_window import (
    MIN_TEST_FOLD_SESSIONS,
)
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    MIN_DATED_TEST_FOLD_DAYS,
    MIN_TEST_FOLD_DAYS,
    FoldWindow,
    WalkForwardStage,
    WalkForwardStageConfig,
)
from autonomous_trading_platform.runtime.clock import RealMarketCalendar
from tests.research.pipeline.test_robustness_stages import PERMISSIVE, SYMBOLS

START = date(2024, 1, 2)
END = date(2025, 12, 31)
TRAIN = (date(2024, 1, 2), date(2024, 6, 28))
# A Monday, and the Friday twelve weeks of sessions later: 82 days counting both.
TEST_FIRST = date(2024, 7, 1)
TEST_LAST = date(2024, 9, 20)


def _config(**overrides: Any) -> WalkForwardStageConfig:
    fields: dict[str, Any] = dict(
        name="wf",
        symbols=SYMBOLS,
        start_date=START,
        end_date=END,
        train_filter_config=PERMISSIVE,
        train_scoring_weights=ScoringWeights(),
        test_filter_config=PERMISSIVE,
        test_scoring_weights=ScoringWeights(),
    )
    fields.update(overrides)
    return WalkForwardStageConfig(**fields)


def _rolling(test_days: int, **overrides: Any) -> WalkForwardStageConfig:
    return _config(train_days=180, test_days=test_days, step_days=test_days, **overrides)


def _dated(*test_windows: tuple[date, date], **overrides: Any) -> WalkForwardStageConfig:
    folds = tuple(
        FoldWindow(train_start=TRAIN[0], train_end=TRAIN[1], test_start=first, test_end=last)
        for first, last in test_windows
    )
    return _config(folds=folds, **overrides)


class TestTheNumbers:
    def test_twelve_weeks(self) -> None:
        assert MIN_TEST_FOLD_DAYS == 12 * 7
        assert (TEST_FIRST.weekday(), TEST_LAST.weekday()) == (0, 4)
        assert (TEST_LAST - TEST_FIRST).days + 1 == MIN_DATED_TEST_FOLD_DAYS

    def test_every_sixty_sessions_of_the_real_calendar_clear_the_dated_minimum(self) -> None:
        """So a fold the admission layout accepts is never refused by the config."""
        sessions = list(RealMarketCalendar().trading_days(date(2024, 1, 2), date(2026, 12, 31)))
        spans = [
            (last - first).days + 1
            for first, last in zip(sessions, sessions[MIN_TEST_FOLD_SESSIONS - 1 :], strict=False)
        ]

        assert len(spans) > 600
        assert min(spans) >= MIN_DATED_TEST_FOLD_DAYS


class TestRollingFolds:
    def test_twelve_weeks_is_accepted(self) -> None:
        assert _rolling(MIN_TEST_FOLD_DAYS).test_days == 84

    def test_a_day_under_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"test_days \(83\) is under 84 calendar days"):
            _rolling(MIN_TEST_FOLD_DAYS - 1)

    def test_the_refusal_names_the_exception(self) -> None:
        with pytest.raises(ValueError, match="allow_short_test_folds=True"):
            _rolling(10)

    def test_a_config_that_says_so_keeps_its_short_folds(self) -> None:
        config = _rolling(10, allow_short_test_folds=True)

        assert len(config.fold_windows()) > 1


class TestFoldsGivenByDate:
    def test_sixty_sessions_from_a_monday_are_accepted(self) -> None:
        assert len(_dated((TEST_FIRST, TEST_LAST)).fold_windows()) == 1

    def test_a_day_under_is_refused_and_the_fold_is_named(self) -> None:
        short_last = TEST_LAST - timedelta(days=1)

        with pytest.raises(ValueError, match=r"fold 1's test window \(2024-07-01 to 2024-09-19\)"):
            _dated((TEST_FIRST, short_last))

    def test_the_short_fold_is_the_one_named(self) -> None:
        second_first = TEST_LAST + timedelta(days=3)
        second_last = second_first + timedelta(days=20)

        with pytest.raises(ValueError, match=r"fold 2's test window .* spans 21 calendar days"):
            _dated((TEST_FIRST, TEST_LAST), (second_first, second_last))

    def test_a_config_that_says_so_keeps_its_short_folds(self) -> None:
        config = _dated((TEST_FIRST, TEST_FIRST + timedelta(days=9)), allow_short_test_folds=True)

        assert len(config.fold_windows()) == 1


class TestWhereTheExceptionIsSet:
    def test_a_stage_built_from_a_dict_is_held_to_the_rule(self) -> None:
        raw: dict[str, Any] = dict(
            name="wf",
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
            train_days=180,
            test_days=30,
            step_days=30,
        )

        with pytest.raises(ValueError, match="allow_short_test_folds=True"):
            WalkForwardStage.from_dict(raw, simulation_runner=None)  # type: ignore[arg-type]

        stage = WalkForwardStage.from_dict(
            {**raw, "allow_short_test_folds": True},
            simulation_runner=None,  # type: ignore[arg-type]
        )
        assert stage.stage_name == "wf"

    def test_the_hooks_rolling_layout_says_so_and_needs_to(self) -> None:
        """When that layout's folds reach twelve weeks, the exception comes out."""
        layout = rh._ROLLING_FOLD_LAYOUT

        assert layout["allow_short_test_folds"] is True
        assert layout["test_days"] < MIN_TEST_FOLD_DAYS
