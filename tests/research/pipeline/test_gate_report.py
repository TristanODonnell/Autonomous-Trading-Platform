"""The gate report: what every stage said about every strategy, written down for the
research step's summary (found missing 2026-11-22: a report-only gate's findings reached
no reader).

The stage results here are built by hand, for the report's own rules. The report of a
real six-stage run is checked against that run in
``tests/research/test_research_hooks_admission_pass_rule.py``.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
    MetricDistribution,
    MonteCarloAggregation,
)
from autonomous_trading_platform.research.pipeline.gate_report import (
    build_gate_report,
    gate_report_not_built,
)
from autonomous_trading_platform.research.pipeline.gates.regime_gate import (
    RegimeDimensionCheck,
    RegimeGateVerdict,
)
from autonomous_trading_platform.research.pipeline.stages._gate_stage_support import (
    gate_filter_output,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import (
    StageDiagnostics,
    StageResult,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
)
from tests.research.pipeline.test_robustness_stages import _cfg

LOGGER = "autonomous_trading_platform.research.pipeline.gate_report"


def _stage(
    name: str,
    failures: dict[str, list[str]],
    diagnostics: dict[str, StageDiagnostics] | None = None,
) -> StageResult:
    """A stage that was handed ``failures``' strategies and failed the ones with a reason."""
    return StageResult(
        stage_name=name,
        filter_outputs=[
            gate_filter_output(sid, passed=not reasons, failure_reasons=reasons)
            for sid, reasons in failures.items()
        ],
        survivors=[_cfg(sid) for sid, reasons in failures.items() if not reasons],
        diagnostics=diagnostics or {},
    )


def _by_id(report: dict) -> dict[str, dict]:
    return {record["strategy_id"]: record for record in report["strategies"]}


def _fold(index: int, *, test_sharpe: float, passed: bool) -> FoldValidationInput:
    return FoldValidationInput(
        fold_index=index,
        train_sharpe=1.5,
        test_sharpe=test_sharpe,
        train_drawdown=-0.05,
        test_drawdown=-0.08,
        train_return=0.10,
        test_return=0.02 if passed else -0.03,
        train_passed=True,
        test_passed=passed,
        fold_passed=passed,
    )


def _distribution(**overrides: float) -> MetricDistribution:
    values = {"mean": 0.8, "median": 0.7, "std": 0.3, "min": -0.2, "max": 1.9, "p05": 0.1}
    return MetricDistribution(**{**values, "p95": 1.5, **overrides})


def _two_stages() -> list[StageResult]:
    return [
        _stage("first", {"a": [], "b": ["Sharpe 0.10 < 0.50"], "c": []}),
        _stage("second", {"a": [], "c": ["drawdown -0.45 < -0.40"]}),
    ]


def test_a_strategy_is_recorded_at_every_stage_it_entered_and_no_further() -> None:
    report = build_gate_report(_two_stages())

    assert report["stages"] == ["first", "second"]
    # In the order they entered.
    assert [record["strategy_id"] for record in report["strategies"]] == ["a", "b", "c"]
    records = _by_id(report)
    assert records["a"] == {
        "strategy_id": "a",
        "passed_every_stage": True,
        "eliminated_at": None,
        "stages": [
            {"stage": "first", "passed": True, "failures": []},
            {"stage": "second", "passed": True, "failures": []},
        ],
    }
    assert records["b"] == {
        "strategy_id": "b",
        "passed_every_stage": False,
        "eliminated_at": "first",
        # It never entered the second stage, so the second stage said nothing about it.
        "stages": [{"stage": "first", "passed": False, "failures": ["Sharpe 0.10 < 0.50"]}],
    }
    assert records["c"]["eliminated_at"] == "second"
    assert records["c"]["stages"][1] == {
        "stage": "second",
        "passed": False,
        "failures": ["drawdown -0.45 < -0.40"],
    }


def test_a_pipeline_that_ran_out_of_survivors_admits_nobody() -> None:
    """The runner stops at a stage that lets nobody through: the stages after it are not
    in the results, and passing every stage that ran is not passing every stage."""
    report = build_gate_report([_stage("first", {"a": ["no trades"], "b": ["no trades"]})])

    assert [record["passed_every_stage"] for record in report["strategies"]] == [False, False]
    assert [record["eliminated_at"] for record in report["strategies"]] == ["first", "first"]


def test_no_stage_results_is_an_empty_report() -> None:
    assert build_gate_report([]) == {"stages": [], "strategies": []}


def test_a_stage_with_a_verdict_per_fold_gives_the_folds_and_not_a_list_of_reasons() -> None:
    """Walk-forward writes a verdict per fold and window. Their reasons, put in one
    list, would not say which fold each belongs to; the folds do."""
    folds = [
        _fold(0, test_sharpe=-0.4, passed=False),
        _fold(1, test_sharpe=0.9, passed=True),
        _fold(2, test_sharpe=1.1, passed=True),
    ]
    stage = StageResult(
        stage_name="walk_forward",
        filter_outputs=[
            gate_filter_output("a", passed=True, failure_reasons=[]),
            gate_filter_output("a", passed=False, failure_reasons=["Sharpe -0.40 < 0.00"]),
            gate_filter_output("a", passed=True, failure_reasons=[]),
        ],
        survivors=[_cfg("a")],
        diagnostics={"a": StageDiagnostics(fold_inputs=folds)},
    )

    (record,) = build_gate_report([stage])["strategies"]
    (entry,) = record["stages"]

    assert "failures" not in entry
    assert entry["passed"] is True
    assert entry["folds_passed"] == 2
    assert [(fold["fold"], fold["passed"], fold["test_sharpe"]) for fold in entry["folds"]] == [
        (0, False, -0.4),
        (1, True, 0.9),
        (2, True, 1.1),
    ]


def test_a_report_only_gate_passes_the_strategy_and_its_findings_are_written_down() -> None:
    """The case the report exists for: nothing in the funnel's counts shows this."""
    finding = "trend: worst regime 'down' Sharpe -1.20 < -0.50"
    verdict = RegimeGateVerdict(
        strategy_id="a",
        passed=True,
        dimension_checks=(
            RegimeDimensionCheck(
                dimension="trend",
                evaluable_labels=("up", "down"),
                worst_label="down",
                worst_sharpe=-1.2,
                worst_drawdown=-0.11,
                positive_fraction=0.5,
                evaluated=True,
                failures=(finding,),
            ),
        ),
        findings=(finding,),
        enforced=False,
    )
    stage = _stage("regime", {"a": []}, {"a": StageDiagnostics(regime_verdict=verdict)})

    (record,) = build_gate_report([stage])["strategies"]
    (entry,) = record["stages"]

    assert record["passed_every_stage"] is True
    assert entry["passed"] is True and entry["failures"] == []
    assert entry["regime"] == {
        "passed": True,
        "enforced": False,
        "findings": [finding],
        "failure_reasons": [],
        "warnings": [],
        "dimensions": [
            {
                "dimension": "trend",
                "evaluated": True,
                "labels": ["up", "down"],
                "worst_label": "down",
                "worst_sharpe": -1.2,
                "worst_drawdown": -0.11,
                "positive_fraction": 0.5,
                "failures": [finding],
            }
        ],
    }


def test_the_report_can_be_written_as_json_whatever_the_numbers_are() -> None:
    """A Sharpe over a flat curve is not a finite number and JSON has none; nor has it a
    numpy scalar, which is what the aggregations hand over."""
    aggregation = MonteCarloAggregation(
        strategy_id="a",
        n_runs=np.int64(200),  # type: ignore[arg-type]
        n_passed=np.int64(150),  # type: ignore[arg-type]
        pass_rate=np.float64(0.75),
        passed=np.bool_(True),  # type: ignore[arg-type]
        sharpe_dist=_distribution(mean=float("nan"), max=float("inf")),
        return_dist=_distribution(min=np.float32(-0.5)),  # type: ignore[arg-type]
        drawdown_dist=_distribution(),
        prob_positive_sharpe=0.9,
        prob_sharpe_above_min=0.8,
        n_sessions=188,
    )
    stage = _stage("monte_carlo", {"a": []}, {"a": StageDiagnostics(mc_aggregation=aggregation)})

    report = build_gate_report([stage])

    assert json.loads(json.dumps(report, allow_nan=False)) == report
    block = report["strategies"][0]["stages"][0]["monte_carlo"]
    assert block["sharpe"]["mean"] is None and block["sharpe"]["max"] is None
    assert block["sharpe"]["median"] == 0.7
    assert (block["paths"], block["paths_passed"]) == (200, 150)
    assert type(block["paths"]) is int and block["passed"] is True
    assert block["pass_rate"] == 0.75
    assert block["total_return"]["min"] == -0.5


def test_the_log_has_a_line_for_each_strategy_that_got_past_the_first_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A job's log is where a run is first read. The first stage's failures stay in the
    report alone: a large grid has many of them."""
    with caplog.at_level(logging.INFO, logger=LOGGER):
        build_gate_report(_two_stages())

    lines = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert lines == [
        "gate_report | 3 strategies | 1 passed every stage",
        "gate_report | a | passed every stage | second: passed",
        "gate_report | c | eliminated at second | second: FAILED (drawdown -0.45 < -0.40)",
    ]


def test_a_report_that_was_not_built_says_why() -> None:
    assert gate_report_not_built("failed: boom") == {
        "stages": [],
        "strategies": [],
        "not_built": "failed: boom",
    }
