"""
Tests that MonteCarloStage passes stage_name and unique mc_run_N window_role
values to the SimulationRunner for every trial so artifacts cannot collide.
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
from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
    MonteCarloStage,
    MonteCarloStageConfig,
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
    random_seed: int


class CapturingSimulationRunner:
    def __init__(self) -> None:
        self.captured: list[CapturedRequest] = []

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        self.captured.append(
            CapturedRequest(
                stage_name=request.stage_name,
                window_role=request.window_role,
                strategy_id=request.strategy_id,
                random_seed=request.random_seed,
            )
        )
        return _make_passing_sim_result(request.strategy_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_passing_sim_result(strategy_id: str) -> SimulationRunResult:
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


def _make_mc_stage(runner: CapturingSimulationRunner, n_runs: int = 5) -> MonteCarloStage:
    cfg = MonteCarloStageConfig(
        name="elite_monte_carlo",
        symbols=["AAPL"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 30),
        n_runs=n_runs,
        min_pass_rate=0.50,
        filter_config=_liberal_filter_config(),
        scoring_weights=_liberal_scoring_weights(),
    )
    return MonteCarloStage(stage_config=cfg, simulation_runner=runner)  # type: ignore[arg-type]


def _make_strategy(strategy_id: str = "strat-1") -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_monte_carlo_all_requests_include_stage_name() -> None:
    runner = CapturingSimulationRunner()
    stage = _make_mc_stage(runner, n_runs=5)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    assert len(runner.captured) == 5
    for req in runner.captured:
        assert req.stage_name == "elite_monte_carlo", (
            f"MC request missing stage_name, got: {req.stage_name}"
        )


def test_monte_carlo_each_run_has_unique_window_role() -> None:
    n_runs = 5
    runner = CapturingSimulationRunner()
    stage = _make_mc_stage(runner, n_runs=n_runs)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    window_roles = [r.window_role for r in runner.captured]
    assert len(set(window_roles)) == n_runs, (
        f"Expected {n_runs} distinct window_roles, got: {window_roles}"
    )


def test_monte_carlo_window_roles_follow_mc_run_n_pattern() -> None:
    n_runs = 4
    runner = CapturingSimulationRunner()
    stage = _make_mc_stage(runner, n_runs=n_runs)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    window_roles = sorted(r.window_role for r in runner.captured if r.window_role is not None)
    expected = [f"mc_run_{i}" for i in range(n_runs)]
    assert window_roles == expected


def test_monte_carlo_each_run_has_unique_seed() -> None:
    n_runs = 5
    base_seed = 200
    runner = CapturingSimulationRunner()
    stage = _make_mc_stage(runner, n_runs=n_runs)

    stage.run(
        survivors=[_make_strategy()],
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=base_seed,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    seeds = [r.random_seed for r in runner.captured]
    assert len(set(seeds)) == n_runs, f"Expected {n_runs} unique seeds, got: {seeds}"
    assert sorted(seeds) == [base_seed + i for i in range(n_runs)]


def test_monte_carlo_multiple_strategies_all_get_separate_runs() -> None:
    n_runs = 3
    runner = CapturingSimulationRunner()
    stage = _make_mc_stage(runner, n_runs=n_runs)

    strategies = [_make_strategy(f"strat-{i}") for i in range(2)]

    stage.run(
        survivors=strategies,
        experiment_id="exp-1",
        dataset_version="v1",
        random_seed=100,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )

    # 2 strategies × 3 runs each = 6 total requests
    assert len(runner.captured) == 2 * n_runs

    for strategy_id in ["strat-0", "strat-1"]:
        strategy_runs = [r for r in runner.captured if r.strategy_id == strategy_id]
        assert len(strategy_runs) == n_runs
        window_roles = [r.window_role for r in strategy_runs]
        assert len(set(window_roles)) == n_runs, (
            f"Expected {n_runs} unique window_roles for {strategy_id}"
        )
