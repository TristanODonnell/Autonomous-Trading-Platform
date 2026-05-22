from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
    simulation_request_checkpoint_identity,
)
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.pipeline.stages.simulation_stage import (
    SimulationStage,
    SimulationStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class CountingRunner:
    def __init__(self) -> None:
        self.requests: list[SimulationRunRequest] = []

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        self.requests.append(request)
        result = MagicMock(spec=SimulationRunResult)
        result.strategy_id = request.strategy_id
        result.run_id = MagicMock()
        result.return_metrics = MagicMock(total_return=0.1)
        result.risk_metrics = MagicMock(sharpe_ratio=2.0, max_drawdown=-0.05, volatility=0.1)
        result.trade_metrics = MagicMock(total_trades=50, win_rate=0.6, profit_factor=1.5)
        result.stability_metrics = MagicMock(consistency_score=0.8)
        result.equity_curve = MagicMock()
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


def _stage(runner: CountingRunner, service: ResearchCheckpointService) -> SimulationStage:
    return SimulationStage(
        stage_config=SimulationStageConfig(
            name="cheap",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            symbols=["AAPL"],
            filter_config=_filter(),
            scoring_weights=ScoringWeights(),
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
        checkpoint_service=service,
    )


def _strategy() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="strat-1",
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


def test_pipeline_stage_skips_completed_simulation() -> None:
    service = ResearchCheckpointService()
    request = SimulationRunRequest(
        experiment_id="exp-1",
        strategy_id="strat-1",
        strategy_config=_strategy().model_dump(),
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        initial_cash=100_000.0,
        stage_name="cheap",
        window_role=None,
    )
    service.mark_completed(simulation_request_checkpoint_identity(request))
    runner = CountingRunner()

    result = _stage(runner, service).run(
        survivors=[_strategy()],
        experiment_id="exp-1",
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    assert runner.requests == []
    assert result.survivors == [_strategy()]


def test_pipeline_stage_dry_run_performs_no_execution() -> None:
    service = ResearchCheckpointService()
    runner = CountingRunner()
    stage = _stage(runner, service)
    stage._dry_run = True

    stage.run(
        survivors=[_strategy()],
        experiment_id="exp-1",
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    assert runner.requests == []
