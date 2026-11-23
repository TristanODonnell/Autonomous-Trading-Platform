"""Daily-bar simulation is switched off until its two defects are fixed (research plan R2).

A run with ``resample_to_daily`` loads no bars from before its window, and its annualised
numbers are scaled with the five-minute bar count, so a Sharpe reads about 8.8 times too
high. Both are silent, and nothing used the mode, so asking for it is an error now.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pytest

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.pipeline.pipeline_runner import PipelineRunner
from autonomous_trading_platform.research.pipeline.stages.base_stage import BaseStage, StageResult
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStage,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    DAILY_RESAMPLE_REFUSED,
    DailyResampleRefusedError,
    SimulationRunRequest,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from tests.research.pipeline.test_robustness_stages import ScriptedRunner, _cfg
from tests.research.pipeline.test_walk_forward_folds_by_date import _config, _script


def _request(**overrides: Any) -> SimulationRunRequest:
    fields: dict[str, Any] = dict(
        strategy_id="s",
        strategy_config={"type": "moving_average_crossover", "parameters": {}},
        dataset_version="v1",
        random_seed=1,
        price_basis=PriceBasis.RAW,
        symbols=["SPY"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 3, 29),
    )
    fields.update(overrides)
    return SimulationRunRequest(**fields)


class _Records(BaseStage):
    def __init__(self) -> None:
        self.ran = False

    @property
    def stage_name(self) -> str:
        return "records"

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
        self.ran = True
        return StageResult(stage_name="records", survivors=list(survivors))

    @classmethod
    def from_dict(cls, raw: Any, simulation_runner: Any) -> _Records:
        raise NotImplementedError


_PIPELINE_KWARGS: dict[str, Any] = dict(
    initial_configs=[_cfg("a")],
    experiment_id="exp",
    dataset_version="v1",
    random_seed=7,
    price_basis=PriceBasis.RAW,
    initial_cash=100_000.0,
)


class TestARunRequest:
    def test_five_minute_bars_are_the_default_and_are_accepted(self) -> None:
        assert _request().resample_to_daily is False
        assert _request(resample_to_daily=False).resample_to_daily is False

    def test_daily_bars_are_refused(self) -> None:
        with pytest.raises(DailyResampleRefusedError, match="resample_to_daily is switched off"):
            _request(resample_to_daily=True)

    def test_the_refusal_is_a_value_error_and_gives_both_reasons(self) -> None:
        with pytest.raises(ValueError) as refused:
            _request(resample_to_daily=True)

        message = str(refused.value)
        assert "no bars from before its window" in message
        assert "8.8 times too high" in message


class TestTheNumberInTheMessage:
    def test_a_daily_sharpe_scaled_as_five_minute_bars_is_about_8_8_times_too_high(self) -> None:
        assert BARS_PER_YEAR == 19_656
        assert math.sqrt(BARS_PER_YEAR / 252) == pytest.approx(8.83, abs=0.01)
        assert "19,656" in DAILY_RESAMPLE_REFUSED
        assert "252" in DAILY_RESAMPLE_REFUSED


class TestAPipeline:
    def test_it_is_refused_before_any_stage_runs(self) -> None:
        stage = _Records()

        with pytest.raises(DailyResampleRefusedError):
            PipelineRunner(stages=[stage]).run(**_PIPELINE_KWARGS, resample_to_daily=True)

        assert stage.ran is False

    def test_it_runs_on_five_minute_bars(self) -> None:
        stage = _Records()

        PipelineRunner(stages=[stage]).run(**_PIPELINE_KWARGS)

        assert stage.ran is True


class TestAStageRunOnItsOwn:
    def test_the_refusal_is_raised_not_recorded_as_a_failed_strategy(self) -> None:
        """A stage that swallowed it would report every strategy eliminated."""
        runner = ScriptedRunner(_script({}))
        stage = WalkForwardStage(
            stage_config=_config(),
            simulation_runner=runner,  # type: ignore[arg-type]
        )

        with pytest.raises(DailyResampleRefusedError):
            stage.run(
                survivors=[_cfg("a")],
                experiment_id="exp",
                dataset_version="v1",
                random_seed=7,
                price_basis=PriceBasis.RAW,
                initial_cash=100_000.0,
                resample_to_daily=True,
            )

        assert runner.requests == []
