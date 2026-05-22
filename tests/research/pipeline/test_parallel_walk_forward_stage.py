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
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStage,
    WalkForwardStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunRequest
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class DelayedRunner:
    def run(self, request: SimulationRunRequest):
        if request.strategy_id == "strat-0":
            time.sleep(0.02)
        return _passing_result(request.strategy_id)


def _passing_result(strategy_id: str):
    result = MagicMock()
    result.strategy_id = strategy_id
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


def _stage(mode: ExecutionMode) -> WalkForwardStage:
    cfg = WalkForwardStageConfig(
        name="walk_forward",
        symbols=["AAPL"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 5, 1),
        train_days=60,
        test_days=20,
        step_days=60,
        train_filter_config=_filter_config(),
        train_scoring_weights=ScoringWeights(),
        test_filter_config=_filter_config(),
        test_scoring_weights=ScoringWeights(),
        require_all_folds=False,
        execution_mode=mode,
        max_workers=2,
    )
    return WalkForwardStage(stage_config=cfg, simulation_runner=DelayedRunner())  # type: ignore[arg-type]


def _strategies() -> list[StrategyConfig]:
    return [
        StrategyConfig(
            strategy_id=f"strat-{index}",
            type="moving_average_crossover",
            parameters={"short_window": 5, "long_window": 20 + index},
        )
        for index in range(2)
    ]


def test_parallel_walk_forward_stage_preserves_fold_strategy_order() -> None:
    result = _stage(ExecutionMode.PARALLEL).run(
        survivors=_strategies(),
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000,
    )

    assert [item.strategy_id for item in result.simulation_results[:4]] == [
        "strat-0",
        "strat-0",
        "strat-1",
        "strat-1",
    ]
