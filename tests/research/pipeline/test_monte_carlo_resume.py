from __future__ import annotations

from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpointStatus,
    ResearchTaskType,
)
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
from tests.research.pipeline.monte_carlo_support import CurveRunner, sharpe_runner, strategy

START, END = date(2024, 1, 2), date(2024, 2, 1)


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


def _base_request(config: StrategyConfig) -> SimulationRunRequest:
    """The base run's request as the stage builds it (the identity ignores the seed)."""
    return SimulationRunRequest(
        experiment_id="exp-1",
        strategy_id=config.strategy_id,
        strategy_config=config.model_dump(),
        dataset_version="bars-v1",
        random_seed=0,
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=START,
        end_date=END,
        initial_cash=100_000.0,
        stage_name="elite_monte_carlo",
        window_role="mc_base",
    )


def _stage(runner: CurveRunner, service: ResearchCheckpointService) -> MonteCarloStage:
    return MonteCarloStage(
        stage_config=MonteCarloStageConfig(
            name="elite_monte_carlo",
            symbols=["AAPL"],
            start_date=START,
            end_date=END,
            n_runs=200,
            min_pass_rate=0.5,
            filter_config=_filter(),
            scoring_weights=ScoringWeights(),
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
        checkpoint_service=service,
    )


def _run(stage: MonteCarloStage, config: StrategyConfig):
    return stage.run(
        survivors=[config],
        experiment_id="exp-1",
        dataset_version="bars-v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )


def test_monte_carlo_base_run_executes_once_and_is_checkpointed() -> None:
    service = ResearchCheckpointService()
    config = strategy()
    runner = sharpe_runner({config.strategy_id: 2.0})

    result = _run(_stage(runner, service), config)

    assert len(runner.requests) == 1
    assert result.survivors == [config]
    checkpoint = service.get(
        simulation_request_checkpoint_identity(
            _base_request(config), task_type=ResearchTaskType.MONTE_CARLO_TRIAL
        )
    )
    assert checkpoint is not None
    assert checkpoint.status == ResearchCheckpointStatus.COMPLETED


def test_monte_carlo_completed_base_run_skips_execution_and_fails_closed() -> None:
    """
    A completed checkpoint skips the simulation but carries no result, so there
    is no equity curve to resample and the strategy is eliminated. Skipped units
    are not rehydrated yet (R3 in docs/roadmaps/research-generation-plan.md);
    until they are, "skipped" must not be read as "passed".
    """
    service = ResearchCheckpointService()
    config = strategy()
    service.mark_completed(
        simulation_request_checkpoint_identity(
            _base_request(config), task_type=ResearchTaskType.MONTE_CARLO_TRIAL
        )
    )
    runner = sharpe_runner({config.strategy_id: 2.0})

    result = _run(_stage(runner, service), config)

    assert runner.requests == []
    assert result.survivors == []
    assert result.filter_outputs[0].filter_result.failures == ["no base run available"]
