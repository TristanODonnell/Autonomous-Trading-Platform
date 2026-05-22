"""
Tests that WalkForwardStage passes stage_name and distinct window_role values
(fold_N_train / fold_N_test) to the SimulationRunner for every fold.
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
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStage,
    WalkForwardStageConfig,
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
    experiment_id: str | None


class CapturingSimulationRunner:
    """Records every SimulationRunRequest that passes through it."""

    def __init__(self) -> None:
        self.captured: list[CapturedRequest] = []

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        self.captured.append(
            CapturedRequest(
                stage_name=request.stage_name,
                window_role=request.window_role,
                strategy_id=request.strategy_id,
                experiment_id=request.experiment_id,
            )
        )
        return _make_passing_sim_result(request.strategy_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_passing_sim_result(strategy_id: str) -> SimulationRunResult:

    result = MagicMock(spec=SimulationRunResult)
    result.strategy_id = strategy_id
    result.experiment_id = "exp-1"
    result.run_id = MagicMock()
    result.dataset_version = "v1"
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


def _make_walk_forward_stage(runner: CapturingSimulationRunner) -> WalkForwardStage:
    cfg = WalkForwardStageConfig(
        name="walk_forward",
        symbols=["AAPL"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 7, 1),
        train_days=90,
        test_days=30,
        step_days=90,
        train_filter_config=_liberal_filter_config(),
        train_scoring_weights=_liberal_scoring_weights(),
        test_filter_config=_liberal_filter_config(),
        test_scoring_weights=_liberal_scoring_weights(),
        require_all_folds=False,
        min_folds_passed=1,
    )
    return WalkForwardStage(stage_config=cfg, simulation_runner=runner)  # type: ignore[arg-type]


def _make_strategy(strategy_id: str = "strat-1") -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_walk_forward_train_requests_include_stage_name() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_walk_forward_stage(runner)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    train_requests = [r for r in runner.captured if "train" in (r.window_role or "")]
    assert train_requests, "No train requests captured"
    for req in train_requests:
        assert req.stage_name == "walk_forward", (
            f"Train request missing stage_name, got: {req.stage_name}"
        )


def test_walk_forward_test_requests_include_stage_name() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_walk_forward_stage(runner)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    test_requests = [r for r in runner.captured if "test" in (r.window_role or "")]
    assert test_requests, "No test requests captured"
    for req in test_requests:
        assert req.stage_name == "walk_forward", (
            f"Test request missing stage_name, got: {req.stage_name}"
        )


def test_walk_forward_train_and_test_have_distinct_window_roles() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_walk_forward_stage(runner)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    window_roles = [r.window_role for r in runner.captured]
    train_roles = [r for r in window_roles if r and "train" in r]
    test_roles = [r for r in window_roles if r and "test" in r]

    assert train_roles, "Expected at least one train window_role"
    assert test_roles, "Expected at least one test window_role"

    # No train and test role should be identical
    assert set(train_roles).isdisjoint(set(test_roles)), (
        "Train and test window roles must be distinct to avoid artifact collision"
    )


def test_walk_forward_fold_index_in_window_role() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_walk_forward_stage(runner)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    # Should see fold_0_train and fold_0_test style roles
    window_roles = [r.window_role for r in runner.captured if r.window_role]
    assert any("fold_" in r for r in window_roles), (
        f"Expected fold_N style window roles, got: {window_roles}"
    )


def test_walk_forward_all_requests_carry_experiment_id() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_walk_forward_stage(runner)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-wf-test",
        dataset_version="v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    for req in runner.captured:
        assert req.experiment_id == "exp-wf-test"
