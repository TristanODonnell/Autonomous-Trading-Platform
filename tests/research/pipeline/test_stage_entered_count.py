"""A stage's "entered" count is the number of strategies handed to it (research plan R2).

It was the number of filter verdicts the stage wrote. Walk-forward writes one per strategy
per fold per window, so on the worker box (2026-11-23) three strategies read "entered 12",
and "failed", which is entered less passed, was wrong with it.
"""

from __future__ import annotations

from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.pipeline.pipeline_runner import PipelineRunner
from autonomous_trading_platform.research.pipeline.stages.base_stage import BaseStage, StageResult
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStage,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from tests.research.pipeline.test_robustness_stages import ScriptedRunner, _cfg
from tests.research.pipeline.test_walk_forward_folds_by_date import (
    TEST_1,
    TEST_2,
    TEST_3,
    _config,
    _run_stage,
    _script,
)

# Four strategies over three folds: two pass two of three, two do not.
LOSING = {
    "one_bad": [TEST_3],
    "two_bad": [TEST_1, TEST_2],
    "all_bad": [TEST_1, TEST_2, TEST_3],
}
FOUR = ["clean", "one_bad", "two_bad", "all_bad"]


class _KeepsTheFirst(BaseStage):
    """Passes the first ``keep`` strategies it is handed and writes no verdict at all."""

    def __init__(self, name: str, keep: int) -> None:
        self._name = name
        self._keep = keep
        self.handed: list[str] = []

    @property
    def stage_name(self) -> str:
        return self._name

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> StageResult:
        self.handed = [config.strategy_id for config in survivors]
        return StageResult(stage_name=self._name, survivors=list(survivors[: self._keep]))

    @classmethod
    def from_dict(cls, raw: Any, simulation_runner: Any) -> _KeepsTheFirst:
        raise NotImplementedError


def _pipeline(stages: list[BaseStage], strategies: list[str]) -> list[StageResult]:
    result = PipelineRunner(stages=stages).run(
        initial_configs=[_cfg(sid) for sid in strategies],
        experiment_id="exp",
        dataset_version="v1",
        random_seed=7,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )
    return result.stage_results


class TestWalkForwardOnItsOwn:
    def test_it_counts_strategies_not_verdicts(self) -> None:
        result, _ = _run_stage(LOSING, FOUR)

        assert len(result.filter_outputs) > len(FOUR)  # what the count used to be
        assert result.n_entered == 4
        assert result.n_passed == 2
        assert result.n_failed == 2


class TestAResultBuiltOutsideAPipeline:
    def test_survivors_with_no_verdict_are_counted(self) -> None:
        result = StageResult(stage_name="s", survivors=[_cfg("a"), _cfg("b")])

        assert result.n_entered == 2
        assert result.n_failed == 0

    def test_a_recorded_count_is_the_count(self) -> None:
        result = StageResult(stage_name="s", survivors=[_cfg("a")], entered=5)

        assert result.n_entered == 5
        assert result.n_failed == 4


class TestThePipelineRunnerRecordsWhatItHandsEachStage:
    def test_each_stage_is_counted_on_what_it_was_handed(self) -> None:
        first, second = _KeepsTheFirst("first", keep=2), _KeepsTheFirst("second", keep=1)

        results = _pipeline([first, second], ["a", "b", "c", "d", "e"])

        assert [(r.n_entered, r.n_passed, r.n_failed) for r in results] == [(5, 2, 3), (2, 1, 1)]
        assert second.handed == ["a", "b"]

    def test_walk_forward_in_a_pipeline(self) -> None:
        stage = WalkForwardStage(
            stage_config=_config(),
            simulation_runner=ScriptedRunner(_script(LOSING)),  # type: ignore[arg-type]
        )

        (result,) = _pipeline([stage], FOUR)

        assert result.entered == 4
        assert (result.n_entered, result.n_passed, result.n_failed) == (4, 2, 2)

    def test_a_stage_that_eliminates_everyone_still_reports_who_entered(self) -> None:
        (result,) = _pipeline([_KeepsTheFirst("none", keep=0)], ["a", "b", "c"])

        assert (result.n_entered, result.n_passed, result.n_failed) == (3, 0, 3)
