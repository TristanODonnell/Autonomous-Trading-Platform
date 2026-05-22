from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.checkpoints.research_checkpoint import ResearchTaskType
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
    simulation_request_checkpoint_identity,
)
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


class CountingRunner:
    def __init__(self) -> None:
        self.requests: list[SimulationRunRequest] = []

    def run(self, request: SimulationRunRequest):
        self.requests.append(request)
        result = MagicMock()
        result.strategy_id = request.strategy_id
        return result


def _filter() -> FilterConfig:
    return FilterConfig(
        min_sharpe=-999.0,
        max_drawdown=-1.0,
        min_trades=0,
        min_consistency_score=0.0,
        min_profit_factor=0.0,
        min_win_rate=0.0,
        min_total_return=-999.0,
    )


def _strategy() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="strat-1",
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


def test_walk_forward_completed_fold_units_skip_execution() -> None:
    service = ResearchCheckpointService()
    strategy = _strategy()
    common = {
        "experiment_id": "exp-1",
        "strategy_id": strategy.strategy_id,
        "strategy_config": strategy.model_dump(),
        "dataset_version": "bars-v1",
        "random_seed": 42,
        "price_basis": PriceBasis.RAW,
        "symbols": ["AAPL"],
        "initial_cash": 100_000.0,
        "stage_name": "walk_forward",
    }
    for window_role, start_date, end_date in (
        ("fold_0_train", date(2024, 1, 1), date(2024, 1, 31)),
        ("fold_0_test", date(2024, 1, 31), date(2024, 2, 10)),
    ):
        req = SimulationRunRequest(
            **common,
            start_date=start_date,
            end_date=end_date,
            window_role=window_role,
        )
        service.mark_completed(
            simulation_request_checkpoint_identity(
                req,
                task_type=ResearchTaskType.WALK_FORWARD_FOLD,
            )
        )

    runner = CountingRunner()
    stage = WalkForwardStage(
        stage_config=WalkForwardStageConfig(
            name="walk_forward",
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 10),
            train_days=30,
            test_days=10,
            step_days=30,
            train_filter_config=_filter(),
            train_scoring_weights=ScoringWeights(),
            test_filter_config=_filter(),
            test_scoring_weights=ScoringWeights(),
            require_all_folds=False,
            min_folds_passed=1,
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
        checkpoint_service=service,
    )

    result = stage.run(
        survivors=[strategy],
        experiment_id="exp-1",
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    assert runner.requests == []
    assert result.survivors == [strategy]
