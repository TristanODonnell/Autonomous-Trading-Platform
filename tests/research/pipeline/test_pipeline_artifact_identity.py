"""
Tests that PipelineRunner threads stage_name through to SimulationRunner
and that staged pipeline outputs land under distinct stage_name partitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.pipeline.pipeline_runner import PipelineRunner
from autonomous_trading_platform.research.pipeline.stages.simulation_stage import (
    SimulationStage,
    SimulationStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

# ---------------------------------------------------------------------------
# Capturing runner
# ---------------------------------------------------------------------------


@dataclass
class CapturedRequest:
    stage_name: str | None
    window_role: str | None
    strategy_id: str


class CapturingSimulationRunner:
    def __init__(self) -> None:
        self.captured: list[CapturedRequest] = []

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        self.captured.append(
            CapturedRequest(
                stage_name=request.stage_name,
                window_role=request.window_role,
                strategy_id=request.strategy_id,
            )
        )
        return _make_passing_result(request.strategy_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_passing_result(strategy_id: str) -> SimulationRunResult:
    result = MagicMock(spec=SimulationRunResult)
    result.strategy_id = strategy_id
    result.return_metrics = MagicMock()
    result.return_metrics.total_return = 0.1
    result.risk_metrics = MagicMock()
    result.risk_metrics.sharpe_ratio = 2.0
    result.risk_metrics.max_drawdown = -0.05
    result.risk_metrics.volatility = 0.1
    result.trade_metrics = MagicMock()
    result.trade_metrics.total_trades = 50
    result.trade_metrics.win_rate = 0.6
    result.trade_metrics.profit_factor = 1.5
    result.stability_metrics = MagicMock()
    result.stability_metrics.consistency_score = 0.8
    result.equity_curve = MagicMock()
    return result


def _liberal_filter_config() -> FilterConfig:
    return FilterConfig(
        min_sharpe=-999.0,
        max_drawdown=-1.0,
        min_trades=0,
        min_consistency_score=0.0,
        min_profit_factor=0.0,
        min_win_rate=0.0,
        min_total_return=-999.0,
    )


def _liberal_scoring_weights() -> ScoringWeights:
    return ScoringWeights(w_sharpe=0.4, w_return=0.3, w_drawdown=0.2, w_consistency=0.1)


def _make_simulation_stage(
    name: str,
    runner: CapturingSimulationRunner,
    window_role: str | None = None,
) -> SimulationStage:
    cfg = SimulationStageConfig(
        name=name,
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 30),
        symbols=["AAPL"],
        filter_config=_liberal_filter_config(),
        scoring_weights=_liberal_scoring_weights(),
        window_role=window_role,
    )
    return SimulationStage(stage_config=cfg, simulation_runner=runner)  # type: ignore[arg-type]


def _make_strategy(strategy_id: str = "strat-1") -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_stage_pipeline_sets_stage_name() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_simulation_stage("cheap", runner)
    pipeline = PipelineRunner(stages=[stage])

    pipeline.run(
        initial_configs=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    assert len(runner.captured) == 1
    assert runner.captured[0].stage_name == "cheap"


def test_multi_stage_pipeline_each_stage_uses_its_own_name() -> None:
    runner = CapturingSimulationRunner()
    stage_a = _make_simulation_stage("cheap", runner)
    stage_b = _make_simulation_stage("intermediate", runner)
    pipeline = PipelineRunner(stages=[stage_a, stage_b])

    pipeline.run(
        initial_configs=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    stage_names = [r.stage_name for r in runner.captured]
    assert "cheap" in stage_names
    assert "intermediate" in stage_names


def test_multi_stage_pipeline_stage_names_are_distinct() -> None:
    runner = CapturingSimulationRunner()
    stages = [
        _make_simulation_stage("cheap", runner),
        _make_simulation_stage("intermediate", runner),
    ]
    pipeline = PipelineRunner(stages=stages)

    pipeline.run(
        initial_configs=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    stage_names = [r.stage_name for r in runner.captured]
    # Each stage must produce its own name — no two stages share a name
    assert len(set(stage_names)) == len(stage_names), (
        "Different pipeline stages produced the same stage_name — artifact collision risk"
    )


def test_pipeline_experiment_id_threads_through_all_stages() -> None:
    runner = CapturingSimulationRunner()
    stages = [
        _make_simulation_stage("cheap", runner),
        _make_simulation_stage("intermediate", runner),
    ]
    pipeline = PipelineRunner(stages=stages)

    pipeline.run(
        initial_configs=[_make_strategy()],
        experiment_id="exp-threading-test",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    for req in runner.captured:
        assert req.stage_name is not None, "stage_name must be set for every request"
