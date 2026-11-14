from __future__ import annotations

import time
from datetime import date

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.execution import ExecutionMode
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
    MonteCarloStage,
    MonteCarloStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunRequest
from tests.research.pipeline.monte_carlo_support import CurveRunner, curve_with_sharpe, strategy


def _filter_config() -> FilterConfig:
    return FilterConfig(
        min_sharpe=-999,
        max_drawdown=-1,
        min_trades=0,
        min_consistency_score=0,
        min_profit_factor=0,
        min_win_rate=0,
        min_total_return=-999,
    )


def _stage(runner: CurveRunner, execution_mode: ExecutionMode) -> MonteCarloStage:
    return MonteCarloStage(
        stage_config=MonteCarloStageConfig(
            name="mc",
            symbols=["AAPL"],
            start_date=date(2023, 1, 2),
            end_date=date(2023, 2, 1),
            n_runs=200,
            min_pass_rate=0.5,
            filter_config=_filter_config(),
            scoring_weights=ScoringWeights(),
            execution_mode=execution_mode,
            max_workers=3,
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
    )


def _runner() -> CurveRunner:
    def curve_for(request: SimulationRunRequest) -> pd.DataFrame:
        if request.strategy_id == "strat-0":
            time.sleep(0.05)  # the first strategy finishes last
        return curve_with_sharpe(
            start=request.start_date,
            end=request.end_date,
            sharpe=2.0,
            seed=int(request.strategy_id[-1]),
        )

    return CurveRunner(curve_for)


def _run(stage: MonteCarloStage):
    return stage.run(
        survivors=[strategy(f"strat-{i}") for i in range(3)],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000,
    )


def test_parallel_monte_carlo_base_runs_keep_survivor_order() -> None:
    result = _run(_stage(_runner(), ExecutionMode.PARALLEL))

    expected = ["strat-0", "strat-1", "strat-2"]
    assert [item.strategy_id for item in result.simulation_results] == expected
    assert [output.strategy_id for output in result.filter_outputs] == expected
    assert [config.strategy_id for config in result.survivors] == expected


def test_parallel_and_serial_monte_carlo_agree() -> None:
    parallel = _run(_stage(_runner(), ExecutionMode.PARALLEL))
    serial = _run(_stage(_runner(), ExecutionMode.SERIAL))

    for sid in ("strat-0", "strat-1", "strat-2"):
        assert parallel.diagnostics[sid].mc_aggregation == serial.diagnostics[sid].mc_aggregation
