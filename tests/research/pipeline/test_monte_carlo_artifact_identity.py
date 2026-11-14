"""
Tests that MonteCarloStage sends exactly one base simulation per strategy to
the SimulationRunner, identified by stage_name and the "mc_base" window_role,
with a seed derived from the strategy's identity. n_runs is the number of
resampled paths and never reaches the runner.
"""

from __future__ import annotations

from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
    MonteCarloStage,
    MonteCarloStageConfig,
)
from tests.research.pipeline.monte_carlo_support import CurveRunner, sharpe_runner, strategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _runner(*strategy_ids: str) -> CurveRunner:
    return sharpe_runner(dict.fromkeys(strategy_ids or ("strat-1",), 2.0))


def _make_mc_stage(runner: CurveRunner, n_runs: int = 50) -> MonteCarloStage:
    cfg = MonteCarloStageConfig(
        name="elite_monte_carlo",
        symbols=["AAPL"],
        start_date=date(2023, 1, 2),
        end_date=date(2023, 2, 28),
        n_runs=n_runs,
        min_pass_rate=0.50,
        filter_config=_liberal_filter_config(),
        scoring_weights=ScoringWeights(
            w_sharpe=0.4, w_return=0.3, w_drawdown=0.2, w_consistency=0.1
        ),
    )
    return MonteCarloStage(stage_config=cfg, simulation_runner=runner)  # type: ignore[arg-type]


def _run(
    stage: MonteCarloStage,
    strategy_ids: tuple[str, ...] = ("strat-1",),
    *,
    experiment_id: str = "exp-1",
    random_seed: int = 100,
) -> StageResult:
    return stage.run(
        survivors=[strategy(sid) for sid in strategy_ids],
        experiment_id=experiment_id,
        dataset_version="v1",
        random_seed=random_seed,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_monte_carlo_base_request_includes_stage_name_and_role() -> None:
    runner = _runner()
    _run(_make_mc_stage(runner))

    (request,) = runner.requests
    assert request.stage_name == "elite_monte_carlo"
    assert request.window_role == "mc_base"


def test_monte_carlo_runs_one_simulation_whatever_n_runs_is() -> None:
    for n_runs in (2, 50, 400):
        runner = _runner()
        result = _run(_make_mc_stage(runner, n_runs=n_runs))

        assert len(runner.requests) == 1
        assert len(result.simulation_results) == 1
        aggregation = result.diagnostics["strat-1"].mc_aggregation
        assert aggregation is not None and aggregation.n_runs == n_runs


def test_monte_carlo_base_seed_is_derived_and_stable() -> None:
    def seed(strategy_id: str = "strat-1", **run_kwargs) -> int:
        runner = _runner(strategy_id)
        _run(_make_mc_stage(runner), (strategy_id,), **run_kwargs)
        return runner.requests[0].random_seed

    assert seed() == seed()  # same identity → same seed
    # Not the pipeline seed itself, so neighbouring experiments' base seeds cannot collide.
    assert seed() != 100
    assert seed() != seed(random_seed=101)
    assert seed() != seed(experiment_id="exp-2")
    assert seed() != seed("strat-2")


def test_monte_carlo_multiple_strategies_each_get_one_base_run() -> None:
    ids = ("strat-0", "strat-1", "strat-2")
    runner = _runner(*ids)
    result = _run(_make_mc_stage(runner), ids)

    assert sorted(r.strategy_id for r in runner.requests) == list(ids)
    assert {r.window_role for r in runner.requests} == {"mc_base"}
    assert len({r.random_seed for r in runner.requests}) == len(ids)
    assert [r.strategy_id for r in result.simulation_results] == list(ids)


def test_monte_carlo_base_run_is_recorded_as_the_reference_result() -> None:
    runner = _runner()
    result = _run(_make_mc_stage(runner))

    (base,) = result.simulation_results
    assert result.diagnostics["strat-1"].reference_result is base
