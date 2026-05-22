from __future__ import annotations

import time
from datetime import date
from unittest.mock import MagicMock

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
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class CapturingRunner:
    def run(self, request: SimulationRunRequest):
        if request.window_role == "mc_run_0":
            time.sleep(0.02)
        return _passing_result(request.strategy_id, request.random_seed)


def _passing_result(strategy_id: str, seed: int):
    result = MagicMock()
    result.strategy_id = strategy_id
    result.random_seed = seed
    result.return_metrics.total_return = 0.1
    result.risk_metrics.sharpe_ratio = 2.0
    result.risk_metrics.max_drawdown = -0.05
    result.trade_metrics.total_trades = 50
    result.trade_metrics.win_rate = 0.6
    result.trade_metrics.profit_factor = 1.5
    result.stability_metrics.consistency_score = 0.8
    result.equity_curve = MagicMock()
    return result


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


def test_parallel_monte_carlo_trials_are_ordered_by_trial() -> None:
    stage = MonteCarloStage(
        stage_config=MonteCarloStageConfig(
            name="mc",
            symbols=["AAPL"],
            start_date=date(2023, 1, 1),
            end_date=date(2023, 2, 1),
            n_runs=3,
            min_pass_rate=0.5,
            filter_config=_filter_config(),
            scoring_weights=ScoringWeights(),
            execution_mode=ExecutionMode.PARALLEL,
            max_workers=3,
        ),
        simulation_runner=CapturingRunner(),  # type: ignore[arg-type]
    )

    result = stage.run(
        survivors=[
            StrategyConfig(
                strategy_id="strat-1",
                type="moving_average_crossover",
                parameters={"short_window": 5, "long_window": 20},
            )
        ],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000,
    )

    assert [item.random_seed for item in result.simulation_results] == [100, 101, 102]
