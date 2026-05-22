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
from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
    MonteCarloStage,
    MonteCarloStageConfig,
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


def test_monte_carlo_completed_trials_skip_execution() -> None:
    service = ResearchCheckpointService()
    strategy = _strategy()
    for run_index in range(3):
        req = SimulationRunRequest(
            experiment_id="exp-1",
            strategy_id=strategy.strategy_id,
            strategy_config=strategy.model_dump(),
            dataset_version="bars-v1",
            random_seed=100 + run_index,
            price_basis=PriceBasis.RAW,
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            initial_cash=100_000.0,
            stage_name="elite_monte_carlo",
            window_role=f"mc_run_{run_index}",
        )
        service.mark_completed(
            simulation_request_checkpoint_identity(
                req,
                task_type=ResearchTaskType.MONTE_CARLO_TRIAL,
            )
        )

    runner = CountingRunner()
    stage = MonteCarloStage(
        stage_config=MonteCarloStageConfig(
            name="elite_monte_carlo",
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            n_runs=3,
            min_pass_rate=0.5,
            filter_config=_filter(),
            scoring_weights=ScoringWeights(),
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
        checkpoint_service=service,
    )

    result = stage.run(
        survivors=[strategy],
        experiment_id="exp-1",
        dataset_version="bars-v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    assert runner.requests == []
    assert result.survivors == [strategy]
