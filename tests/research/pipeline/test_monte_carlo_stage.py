"""
MonteCarloStage behaviour with realistic thresholds and real metrics.

One base run per strategy is resampled in whole sessions; the gate is the
share of resampled paths that clear the thresholds. These tests use curves
with a chosen full-window Sharpe so the expected verdicts follow from the
window length (63 sessions → a resampled Sharpe spread of about 2).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreInput,
    FilterScoreService,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import (
    StageDiagnostics,
    StageResult,
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
from tests.research.pipeline.monte_carlo_support import (
    CurveRunner,
    curve_with_sharpe,
    result_from_curve,
    sharpe_runner,
    strategy,
)

START, END = date(2024, 1, 2), date(2024, 3, 28)  # 63 weekdays
SYMBOLS = ["AAA", "BBB"]

# The thresholds ma_sweep_staged.yaml uses for this stage; the per-trade
# quality filters are switched off because the fixture's trade stats are fixed.
REALISTIC = FilterConfig(
    min_sharpe=1.0,
    max_drawdown=-0.20,
    min_trades=10,
    min_consistency_score=0.0,
    min_profit_factor=0.0,
    min_win_rate=0.0,
    min_total_return=0.0,
)


def _stage(
    runner: CurveRunner,
    *,
    n_runs: int = 500,
    min_pass_rate: float = 0.70,
    filter_config: FilterConfig = REALISTIC,
) -> MonteCarloStage:
    return MonteCarloStage(
        stage_config=MonteCarloStageConfig(
            name="mc",
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
            n_runs=n_runs,
            min_pass_rate=min_pass_rate,
            filter_config=filter_config,
            scoring_weights=ScoringWeights(),
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
    )


def _run(
    stage: MonteCarloStage, survivors: list[StrategyConfig], *, random_seed: int = 7
) -> StageResult:
    return stage.run(
        survivors=survivors,
        experiment_id="exp",
        dataset_version="v1",
        random_seed=random_seed,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )


def _reference(
    strategy_id: str, *, start: date = START, sharpe: float = 8.0
) -> SimulationRunResult:
    request = SimulationRunRequest(
        strategy_id=strategy_id,
        strategy_config={},
        dataset_version="v1",
        random_seed=1,
        price_basis=PriceBasis.RAW,
        symbols=list(SYMBOLS),
        start_date=start,
        end_date=END,
    )
    return result_from_curve(
        request, curve_with_sharpe(start=start, end=END, sharpe=sharpe, seed=3)
    )


class TestVerdict:
    def test_distribution_is_real(self) -> None:
        result = _run(_stage(sharpe_runner({"s1": 1.5})), [strategy("s1")])
        agg = result.diagnostics["s1"].mc_aggregation

        assert agg is not None
        assert agg.n_runs == 500
        assert agg.n_sessions == 63
        assert agg.sharpe_dist.std > 1.0  # was exactly 0.0 when runs were re-seeded copies
        assert agg.sharpe_dist.p05 < agg.sharpe_dist.median < agg.sharpe_dist.p95
        assert 0.0 < agg.pass_rate < 1.0
        assert 0.5 < agg.prob_positive_sharpe < 1.0

    def test_verdict_can_differ_from_the_single_run_filter(self) -> None:
        """Both base runs clear min_sharpe=1.0; only the strong one clears it on 70% of paths."""
        runner = sharpe_runner({"strong": 8.0, "marginal": 1.5})
        result = _run(_stage(runner), [strategy("strong"), strategy("marginal")])

        assert [c.strategy_id for c in result.survivors] == ["strong"]

        single_run_filter = FilterScoreService(
            filter_config=REALISTIC, scoring_weights=ScoringWeights()
        )
        for sid in ("strong", "marginal"):
            base = result.diagnostics[sid].reference_result
            assert base is not None
            outputs, _ = single_run_filter.filter_and_rank(
                [
                    FilterScoreInput(
                        strategy_id=sid,
                        rm=base.return_metrics,
                        risk=base.risk_metrics,
                        tm=base.trade_metrics,
                        sm=base.stability_metrics,
                        equity_curve=base.equity_curve,
                    )
                ]
            )
            assert outputs[0].filter_result.passed, f"{sid} should pass on its single run"

        strong = result.diagnostics["strong"].mc_aggregation
        marginal = result.diagnostics["marginal"].mc_aggregation
        assert strong is not None and marginal is not None
        assert strong.passed and strong.pass_rate > 0.95
        assert not marginal.passed and 0.3 < marginal.pass_rate < 0.70
        assert any("sharpe < 1.0" in reason for reason in marginal.failure_reasons)

        by_id = {o.strategy_id: o for o in result.filter_outputs}
        assert by_id["strong"].filter_result.passed
        assert not by_id["marginal"].filter_result.passed

    def test_min_trades_is_checked_on_the_base_run(self) -> None:
        runner = sharpe_runner({"s1": 8.0}, trade_count=3)
        result = _run(_stage(runner), [strategy("s1")])
        agg = result.diagnostics["s1"].mc_aggregation

        assert result.survivors == []
        assert agg is not None and agg.pass_rate > 0.95
        assert agg.failure_reasons[0] == "base run trades 3 < 10"

    def test_same_seed_same_verdict_different_seed_different_paths(self) -> None:
        def aggregation(random_seed: int):
            result = _run(
                _stage(sharpe_runner({"s1": 1.5})), [strategy("s1")], random_seed=random_seed
            )
            return result.diagnostics["s1"].mc_aggregation

        first, again, other = aggregation(7), aggregation(7), aggregation(8)
        assert first == again
        assert first.sharpe_dist != other.sharpe_dist

    def test_empty_survivor_list_is_a_no_op(self) -> None:
        runner = sharpe_runner({})
        result = _run(_stage(runner), [])
        assert result.survivors == [] and result.filter_outputs == []
        assert runner.requests == []


class TestMissingEvidenceFails:
    def test_run_that_returns_nothing_eliminates_the_strategy(self) -> None:
        def curve_for(request: SimulationRunRequest) -> pd.DataFrame | None:
            if request.strategy_id == "ghost":
                return None
            return curve_with_sharpe(start=START, end=END, sharpe=8.0, seed=1)

        result = _run(_stage(CurveRunner(curve_for)), [strategy("ghost"), strategy("real")])

        assert [c.strategy_id for c in result.survivors] == ["real"]
        assert result.n_entered == 2
        ghost = next(o for o in result.filter_outputs if o.strategy_id == "ghost")
        assert not ghost.filter_result.passed
        assert ghost.filter_result.failures == ["no base run available"]
        assert "ghost" not in result.diagnostics

    def test_empty_equity_curve_eliminates_the_strategy(self) -> None:
        class EmptyCurveRunner:
            def run(self, request: SimulationRunRequest) -> SimulationRunResult:
                good = result_from_curve(
                    request, curve_with_sharpe(start=START, end=END, sharpe=8.0, seed=1)
                )
                return replace(good, equity_curve=pd.DataFrame(columns=["timestamp", "equity"]))

        result = _run(_stage(EmptyCurveRunner()), [strategy("s1")])  # type: ignore[arg-type]

        assert result.survivors == []
        assert result.filter_outputs[0].filter_result.failures == ["base run has no equity curve"]

    def test_curve_too_short_to_resample_eliminates_the_strategy(self) -> None:
        one_session = CurveRunner(
            lambda request: curve_with_sharpe(start=START, end=START, sharpe=8.0, seed=1)
        )
        result = _run(_stage(one_session), [strategy("s1")])

        assert result.survivors == []
        (failure,) = result.filter_outputs[0].filter_result.failures
        assert failure.startswith("base run cannot be resampled")
        assert "at least 2" in failure


class TestBaseRunReuse:
    def test_reuses_a_matching_full_window_run(self) -> None:
        runner = sharpe_runner({"s1": 8.0})
        ref = _reference("s1")
        stage = _stage(runner)
        stage.bind_prior_results(
            [
                StageResult(
                    stage_name="regime", diagnostics={"s1": StageDiagnostics(reference_result=ref)}
                )
            ]
        )
        result = _run(stage, [strategy("s1")])

        assert runner.requests == []  # nothing simulated
        assert result.simulation_results == []
        assert result.diagnostics["s1"].reference_result is ref
        assert result.diagnostics["s1"].mc_aggregation is not None
        assert [c.strategy_id for c in result.survivors] == ["s1"]

    def test_simulates_when_the_prior_run_covers_another_window(self) -> None:
        runner = sharpe_runner({"s1": 8.0})
        stage = _stage(runner)
        other_window = _reference("s1", start=START + timedelta(days=7))
        stage.bind_prior_results(
            [
                StageResult(
                    stage_name="regime",
                    diagnostics={"s1": StageDiagnostics(reference_result=other_window)},
                )
            ]
        )
        result = _run(stage, [strategy("s1")])

        assert [r.window_role for r in runner.requests] == ["mc_base"]
        assert len(result.simulation_results) == 1
        assert result.diagnostics["s1"].reference_result is result.simulation_results[0]


@pytest.mark.parametrize("n_runs", [0, 1])
def test_fewer_than_two_paths_is_rejected(n_runs: int) -> None:
    with pytest.raises(ValueError, match="n_runs"):
        _stage(sharpe_runner({}), n_runs=n_runs)
