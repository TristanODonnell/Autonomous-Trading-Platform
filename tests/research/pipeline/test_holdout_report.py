"""The holdout report: each survivor run once over the holdout, numbers reported, no row
left behind and nobody removed (decided 2026-11-15 and 2026-11-20).

The simulations are scripted (a daily return per strategy), so the numbers a report
carries can be worked out from the same script.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.pipeline.admission_window import SessionSpan
from autonomous_trading_platform.research.pipeline.holdout_report import (
    HOLDOUT_STAGE_NAME,
    HOLDOUT_WINDOW_ROLE,
    HoldoutRunSettings,
    holdout_not_scored,
    score_holdout,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from tests.research.pipeline.test_robustness_stages import ScriptedRunner, _cfg

SPAN = SessionSpan(first=date(2026, 5, 18), last=date(2026, 6, 17), sessions=22)
SETTINGS = HoldoutRunSettings(
    experiment_id="replay_research_202606",
    dataset_version="window_dataset",
    random_seed=202606,
    price_basis=PriceBasis.RAW,
    initial_cash=250_000.0,
    resample_to_daily=False,
    symbols=["AAA", "BBB"],
    universe_timeline_version="timeline",
)
DAILY = {"gains": 0.004, "loses": -0.004}


def _daily_return(strategy_id: str, _request: SimulationRunRequest, day: date) -> float:
    return DAILY[strategy_id] + (0.001 if day.toordinal() % 2 else -0.001)


def _report(runner: ScriptedRunner, *strategy_ids: str) -> dict:
    return score_holdout(
        simulation_runner=runner,
        survivors=[_cfg(sid) for sid in strategy_ids],
        span=SPAN,
        settings=SETTINGS,
    )


def test_each_survivor_is_run_once_over_the_holdout_span() -> None:
    runner = ScriptedRunner(_daily_return)

    _report(runner, "gains", "loses")

    assert [r.strategy_id for r in runner.requests] == ["gains", "loses"]
    for request in runner.requests:
        assert (request.start_date, request.end_date) == (SPAN.first, SPAN.last)
        assert (request.stage_name, request.window_role) == (
            HOLDOUT_STAGE_NAME,
            HOLDOUT_WINDOW_ROLE,
        )
        # The pipeline's own settings, and the universe of the holdout's dates.
        assert request.experiment_id == SETTINGS.experiment_id
        assert request.dataset_version == SETTINGS.dataset_version
        assert request.price_basis is PriceBasis.RAW
        assert request.initial_cash == 250_000.0
        assert request.symbols == ["AAA", "BBB"]
        assert request.universe_timeline_version == "timeline"
        assert request.cost_multiplier == 1.0


def test_a_holdout_run_leaves_no_row_behind() -> None:
    """Promotion, demotion and allocation read a strategy's newest run; a holdout run
    is never one of them."""
    runner = ScriptedRunner(_daily_return)

    _report(runner, "gains", "loses")

    assert [r.record_run for r in runner.requests] == [False, False]
    # The default is to record: every other run of the pipeline is a row.
    assert SimulationRunRequest.__dataclass_fields__["record_run"].default is True


def test_the_report_carries_each_survivors_numbers() -> None:
    runner = ScriptedRunner(_daily_return)

    report = _report(runner, "gains", "loses")

    # What the same script gives for the same dates, asked for directly.
    expected = {
        request.strategy_id: ScriptedRunner(_daily_return).run(request)
        for request in runner.requests
    }
    assert report["candidates"] == [
        {
            "strategy_id": sid,
            "sharpe": expected[sid].risk_metrics.sharpe_ratio,
            "total_return": expected[sid].return_metrics.total_return,
            "max_drawdown": expected[sid].risk_metrics.max_drawdown,
            "trade_count": 40,
        }
        for sid in ("gains", "loses")
    ]
    gains, loses = report["candidates"]
    assert gains["total_return"] > 0 > loses["total_return"]
    assert loses["max_drawdown"] < gains["max_drawdown"] <= 0
    assert report["scored"] == 2
    assert report["not_scored"] is None
    assert (report["first"], report["last"], report["sessions"]) == (
        "2026-05-18",
        "2026-06-17",
        22,
    )


def test_a_losing_holdout_removes_nobody() -> None:
    """The report has an entry for every survivor it was handed, in the order given,
    whatever the numbers: it has no way to drop one."""
    survivors = [_cfg("loses"), _cfg("gains")]

    report = score_holdout(
        simulation_runner=ScriptedRunner(_daily_return),
        survivors=survivors,
        span=SPAN,
        settings=SETTINGS,
    )

    assert [c["strategy_id"] for c in report["candidates"]] == ["loses", "gains"]
    assert [c.strategy_id for c in survivors] == ["loses", "gains"]


def test_seeds_are_the_same_on_a_rerun_and_differ_by_strategy() -> None:
    first, second = ScriptedRunner(_daily_return), ScriptedRunner(_daily_return)

    _report(first, "gains", "loses")
    _report(second, "gains", "loses")

    assert [r.random_seed for r in first.requests] == [r.random_seed for r in second.requests]
    assert first.requests[0].random_seed != first.requests[1].random_seed


class _FailsFor(ScriptedRunner):
    def __init__(self, strategy_id: str) -> None:
        super().__init__(_daily_return)
        self._fails_for = strategy_id

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        if request.strategy_id == self._fails_for:
            self.requests.append(request)
            raise RuntimeError("no bars for 2026-06-01")
        return super().run(request)


def test_a_run_that_fails_is_reported_and_the_others_still_run() -> None:
    runner = _FailsFor("gains")

    report = _report(runner, "gains", "loses")

    assert report["candidates"][0] == {"strategy_id": "gains", "error": "no bars for 2026-06-01"}
    assert report["candidates"][1]["strategy_id"] == "loses"
    assert "error" not in report["candidates"][1]
    assert report["scored"] == 1
    assert report["not_scored"] is None


class _Flat(ScriptedRunner):
    """A strategy that never traded: a flat curve, whose Sharpe is not a number."""

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        result = super().run(request)
        result.risk_metrics = replace(result.risk_metrics, sharpe_ratio=float("nan"))
        result.return_metrics = replace(result.return_metrics, total_return=float("inf"))
        return result


def test_a_number_that_is_not_finite_is_reported_as_none() -> None:
    """The summary is written out as JSON, which has no NaN."""
    report = _report(_Flat(_daily_return), "gains")

    (candidate,) = report["candidates"]
    assert candidate["sharpe"] is None
    assert candidate["total_return"] is None
    assert candidate["max_drawdown"] is not None


@pytest.mark.parametrize("span", [SPAN, None])
def test_a_report_that_was_not_scored_says_why(span: SessionSpan | None) -> None:
    report = holdout_not_scored(span, "no strategy passed every stage")

    assert report["not_scored"] == "no strategy passed every stage"
    assert (report["scored"], report["candidates"]) == (0, [])
    assert report["sessions"] == (22 if span is not None else 0)
    assert report["first"] == ("2026-05-18" if span is not None else None)
    # The same keys either way, so a reader of the summary need not ask which it got.
    assert set(report) == set(_report(ScriptedRunner(_daily_return), "gains"))
