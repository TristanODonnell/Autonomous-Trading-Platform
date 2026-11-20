"""Walk-forward folds given by date (research plan R2, the hook layout, piece 2).

The pipeline hands the stage its folds: each a train window and a test window, by first
and last day. Used here the way the admission layout will use it: one train window for
every fold, three test windows after it.

    train   2024-01-02 to 2024-01-31
    test 1  2024-02-01 to 2024-02-16
    test 2  2024-02-19 to 2024-03-08
    test 3  2024-03-11 to 2024-03-29
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.research.pipeline.pipeline_validation import PipelineConfigError
from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
from autonomous_trading_platform.research.pipeline.stages.simulation_stage import (
    SimulationStage,
    SimulationStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    FoldWindow,
    WalkForwardStage,
    WalkForwardStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunRequest
from tests.research.pipeline.test_robustness_stages import (
    PERMISSIVE,
    SYMBOLS,
    ScriptedRunner,
    _cfg,
    _run,
)

START = date(2024, 1, 2)
END = date(2024, 3, 29)
TRAIN = (date(2024, 1, 2), date(2024, 1, 31))
TEST_1 = (date(2024, 2, 1), date(2024, 2, 16))
TEST_2 = (date(2024, 2, 19), date(2024, 3, 8))
TEST_3 = (date(2024, 3, 11), date(2024, 3, 29))

# Passes a window that made money, fails one that lost.
MADE_MONEY = replace(PERMISSIVE, min_total_return=0.0)


def _fold(train: tuple[date, date], test: tuple[date, date]) -> FoldWindow:
    return FoldWindow(
        train_start=train[0], train_end=train[1], test_start=test[0], test_end=test[1]
    )


SHARED_TRAIN = (_fold(TRAIN, TEST_1), _fold(TRAIN, TEST_2), _fold(TRAIN, TEST_3))


def _config(**overrides: Any) -> WalkForwardStageConfig:
    fields: dict[str, Any] = dict(
        name="wf",
        symbols=SYMBOLS,
        start_date=START,
        end_date=END,
        folds=SHARED_TRAIN,
        train_filter_config=MADE_MONEY,
        train_scoring_weights=ScoringWeights(),
        test_filter_config=MADE_MONEY,
        test_scoring_weights=ScoringWeights(),
        require_all_folds=False,
        min_folds_passed=2,
    )
    fields.update(overrides)
    return WalkForwardStageConfig(**fields)


def _script(losing: dict[str, list[tuple[date, date]]]):
    """Every strategy makes money every day, except in the windows named for it."""

    def daily_return(strategy_id: str, _request: SimulationRunRequest, day: date) -> float:
        wobble = 0.0005 if day.toordinal() % 2 else -0.0005
        for first, last in losing.get(strategy_id, []):
            if first <= day <= last:
                return -0.004 + wobble
        return 0.004 + wobble

    return daily_return


def _run_stage(
    losing: dict[str, list[tuple[date, date]]], strategies: list[str], **config: Any
) -> tuple[StageResult, ScriptedRunner]:
    runner = ScriptedRunner(_script(losing))
    stage = WalkForwardStage(
        stage_config=_config(**config),
        simulation_runner=runner,  # type: ignore[arg-type]
    )
    return _run(stage, [_cfg(sid) for sid in strategies]), runner


def _dates(requests: list[SimulationRunRequest], role: str) -> list[tuple[date, date]]:
    return [(r.start_date, r.end_date) for r in requests if r.window_role == role]


class TestTheStageUsesTheFoldsItIsGiven:
    def test_the_folds_are_the_ones_given(self) -> None:
        config = _config()

        assert config.fold_windows() == [
            (*TRAIN, *TEST_1),
            (*TRAIN, *TEST_2),
            (*TRAIN, *TEST_3),
        ]
        assert config.first_test_start == TEST_1[0]

    def test_a_list_of_folds_is_taken_as_well(self) -> None:
        assert _config(folds=list(SHARED_TRAIN)).folds == SHARED_TRAIN

    def test_each_test_window_is_simulated_on_its_own_dates(self) -> None:
        _, runner = _run_stage({}, ["a"])

        assert _dates(runner.requests, "fold_0_test") == [TEST_1]
        assert _dates(runner.requests, "fold_1_test") == [TEST_2]
        assert _dates(runner.requests, "fold_2_test") == [TEST_3]

    def test_rolling_folds_are_laid_out_as_before(self) -> None:
        rolling = _config(folds=None, train_days=30, test_days=20, step_days=20)

        assert rolling.fold_windows()[0] == (
            date(2024, 1, 2),
            date(2024, 2, 1),
            date(2024, 2, 1),
            date(2024, 2, 21),
        )
        assert rolling.first_test_start == date(2024, 2, 1)


class TestATrainWindowIsSimulatedOnce:
    def test_three_folds_that_share_it_ask_for_it_once_per_strategy(self) -> None:
        _, runner = _run_stage({}, ["a", "b"])

        train_requests = [r for r in runner.requests if (r.start_date, r.end_date) == TRAIN]
        assert sorted(r.strategy_id for r in train_requests) == ["a", "b"]
        assert {r.window_role for r in train_requests} == {"fold_0_train"}
        # Per strategy: one train run and three test runs.
        assert len(runner.requests) == 2 * (1 + 3)

    def test_the_results_hold_it_once(self) -> None:
        result, _ = _run_stage({}, ["a", "b"])

        assert len(result.simulation_results) == 2 * (1 + 3)
        assert len({id(r) for r in result.simulation_results}) == 2 * (1 + 3)
        assert len(result.filter_outputs) == 2 * (1 + 3)

    def test_every_fold_reports_the_same_train_numbers(self) -> None:
        result, _ = _run_stage({}, ["a"])

        folds = result.diagnostics["a"].fold_inputs
        assert folds is not None
        assert [f.fold_index for f in folds] == [0, 1, 2]
        assert len({f.train_sharpe for f in folds}) == 1
        assert len({f.train_return for f in folds}) == 1

    def test_train_windows_that_differ_are_each_simulated(self) -> None:
        early = (date(2024, 1, 2), date(2024, 1, 16))
        later = (date(2024, 1, 17), date(2024, 1, 31))
        _, runner = _run_stage(
            {}, ["a"], folds=(_fold(early, TEST_1), _fold(later, TEST_2), _fold(early, TEST_3))
        )

        assert _dates(runner.requests, "fold_0_train") == [early]
        assert _dates(runner.requests, "fold_1_train") == [later]
        assert _dates(runner.requests, "fold_2_train") == []


class TestTwoOfThreeMeansTwoOfTheThreeTestPeriods:
    """The reason the train window is the selection piece (decided 2026-11-20): with one
    train window for all folds, a fold's verdict is its own test period's."""

    def test_losing_the_first_test_period_alone_still_passes(self) -> None:
        result, _ = _run_stage({"a": [TEST_1]}, ["a"])

        assert [c.strategy_id for c in result.survivors] == ["a"]
        folds = result.diagnostics["a"].fold_inputs
        assert folds is not None
        assert [f.fold_passed for f in folds] == [False, True, True]

    def test_losing_the_middle_test_period_alone_still_passes(self) -> None:
        result, _ = _run_stage({"a": [TEST_2]}, ["a"])

        assert [c.strategy_id for c in result.survivors] == ["a"]

    def test_losing_two_test_periods_fails(self) -> None:
        result, _ = _run_stage({"a": [TEST_1, TEST_3]}, ["a"])

        assert result.survivors == []

    def test_each_strategy_is_judged_on_its_own_periods(self) -> None:
        result, _ = _run_stage(
            {"one_bad": [TEST_3], "two_bad": [TEST_1, TEST_2], "all_bad": [TEST_1, TEST_2, TEST_3]},
            ["clean", "one_bad", "two_bad", "all_bad"],
        )

        assert [c.strategy_id for c in result.survivors] == ["clean", "one_bad"]


class TestAStrategyThatFailsTheTrainWindow:
    def test_passes_no_fold_and_its_test_periods_are_still_on_record(self) -> None:
        result, runner = _run_stage({"a": [TRAIN]}, ["a"])

        assert result.survivors == []
        folds = result.diagnostics["a"].fold_inputs
        assert folds is not None
        assert [f.train_passed for f in folds] == [False, False, False]
        assert [f.test_passed for f in folds] == [True, True, True]
        assert [f.fold_passed for f in folds] == [False, False, False]
        assert len([r for r in runner.requests if (r.window_role or "").endswith("_test")]) == 3


class TestRefused:
    def test_day_counts_given_together_with_folds(self) -> None:
        with pytest.raises(ValueError, match="train_days, test_days and step_days do not apply"):
            _config(train_days=30, test_days=20, step_days=20)

    def test_the_overlap_exception_together_with_folds(self) -> None:
        with pytest.raises(ValueError, match="allow_overlapping_test_folds has no meaning"):
            _config(allow_overlapping_test_folds=True)

    def test_an_empty_list_of_folds(self) -> None:
        with pytest.raises(ValueError, match="folds is empty"):
            _config(folds=())

    def test_a_train_window_that_ends_on_its_test_windows_first_day(self) -> None:
        shared_day = (TRAIN[0], TEST_1[0])
        with pytest.raises(ValueError, match="fold 1: the train window ends on 2024-02-01"):
            _config(folds=(_fold(shared_day, TEST_1),), min_folds_passed=1)

    def test_a_window_that_ends_before_it_starts(self) -> None:
        backwards = (TEST_1[1], TEST_1[0])
        with pytest.raises(ValueError, match="fold 1: a window ends before it starts"):
            _config(folds=(_fold(TRAIN, backwards),), min_folds_passed=1)

    def test_two_test_windows_that_share_a_day(self) -> None:
        overlapping = (TEST_1[1], TEST_2[1])
        with pytest.raises(ValueError, match="test windows must be in order and share no day"):
            _config(folds=(_fold(TRAIN, TEST_1), _fold(TRAIN, overlapping)))

    def test_test_windows_out_of_order(self) -> None:
        with pytest.raises(ValueError, match="test windows must be in order and share no day"):
            _config(folds=(_fold(TRAIN, TEST_2), _fold(TRAIN, TEST_1)))

    def test_a_train_window_on_another_folds_test_days(self) -> None:
        """Rolling windows written out by date: fold 2 trains on fold 1's test period."""
        with pytest.raises(ValueError, match="one test period would decide two folds") as refusal:
            _config(folds=(_fold(TRAIN, TEST_1), _fold(TEST_1, TEST_2), _fold(TEST_2, TEST_3)))

        assert "fold 2's train window" in str(refusal.value)
        assert "fold 1's test window" in str(refusal.value)

    def test_a_train_window_that_only_touches_another_folds_test_window(self) -> None:
        touching = (TRAIN[0], TEST_1[1])
        with pytest.raises(ValueError, match="one test period would decide two folds"):
            _config(folds=(_fold(TRAIN, TEST_1), _fold(touching, TEST_2)))

    def test_a_fold_outside_the_stages_window(self) -> None:
        with pytest.raises(ValueError, match="lies outside the stage's window"):
            _config(end_date=date(2024, 3, 28))
        with pytest.raises(ValueError, match="lies outside the stage's window"):
            _config(start_date=date(2024, 1, 3))

    def test_more_folds_asked_to_pass_than_there_are(self) -> None:
        with pytest.raises(ValueError, match=r"min_folds_passed \(4\) > the 3 fold"):
            _config(min_folds_passed=4)

    def test_neither_folds_nor_day_counts(self) -> None:
        with pytest.raises(ValueError, match="train_days must be positive"):
            _config(folds=None)


class TestThePipelineRuleSeesTheFirstTestDay:
    """The rule that selection ends before the first test fold reads the folds given."""

    _REAL = FilterConfig()

    def _stages(self, selection_end: date) -> list:
        runner = ScriptedRunner(_script({}))
        return [
            SimulationStage(
                stage_config=SimulationStageConfig(
                    name="select",
                    start_date=START,
                    end_date=selection_end,
                    symbols=SYMBOLS,
                    filter_config=self._REAL,
                    scoring_weights=ScoringWeights(),
                    max_workers=1,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
            WalkForwardStage(
                stage_config=_config(train_filter_config=self._REAL, test_filter_config=self._REAL),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
        ]

    def test_selection_on_the_train_window_is_accepted(self) -> None:
        assert len(StagedPipelineConfig(stages=self._stages(TRAIN[1])).stages) == 2

    def test_selection_reaching_the_first_test_day_is_refused(self) -> None:
        with pytest.raises(PipelineConfigError, match="starts its first test fold on 2024-02-01"):
            StagedPipelineConfig(stages=self._stages(TEST_1[0]))
