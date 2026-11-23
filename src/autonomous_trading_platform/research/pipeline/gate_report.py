"""The gate report: what every stage said about every strategy, written down.

A staged run keeps, for each stage, who survived it and (in ``StageResult.diagnostics``)
the evidence the stage worked out for each strategy: the folds, the Monte Carlo paths,
the regime, stress and overfitting verdicts. The research step used to report only the
funnel's counts, and the evidence was dropped when the process ended. A gate that is
report-only eliminates nobody, so its findings showed in no count either: they reached
no reader at all (found 2026-11-22, on the first baseline's eight survivors).

This module turns that evidence into one record per strategy for the research step's
summary. It reads the stage results and nothing else: no simulation, no database, no
effect on the survivors. Like the holdout report, it is not a stage.

Shape::

    {"stages": [stage names, in order],
     "strategies": [{"strategy_id", "passed_every_stage", "eliminated_at",
                     "stages": [{"stage", "passed", "failures"?, evidence...}]}]}

``failures`` is the stage's own verdict on the strategy, given when the stage wrote
exactly one (walk-forward writes one per fold and window: its ``folds`` say which
passed). The evidence keys are ``reference_run``, ``folds`` and ``folds_passed``,
``monte_carlo``, ``regime``, ``stress`` and ``overfitting``; a stage entry carries the
ones its stage recorded. A report-only gate has ``passed: true`` and its ``findings``.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from autonomous_trading_platform.observability.logging import get_logger

if TYPE_CHECKING:
    from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
        MetricDistribution,
        MonteCarloAggregation,
    )
    from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
        OverfittingGateVerdict,
    )
    from autonomous_trading_platform.research.pipeline.gates.regime_gate import RegimeGateVerdict
    from autonomous_trading_platform.research.pipeline.gates.stress_gate import StressGateVerdict
    from autonomous_trading_platform.research.simulation.simulation_runner import (
        SimulationRunResult,
    )
    from autonomous_trading_platform.research.validation.walk_forward_validation import (
        FoldValidationInput,
    )

    from .stages.base_stage import StageDiagnostics, StageResult

logger = get_logger(__name__)


def build_gate_report(stage_results: Sequence[StageResult]) -> dict[str, Any]:
    """One record per strategy that entered the pipeline, in the order it entered.

    A stage is handed the survivors of the stage before it. The first stage's entrants
    are the strategies its result names, since a result does not list who entered.
    """
    records: dict[str, dict[str, Any]] = {}
    entrants: list[str] | None = None
    for stage_result in stage_results:
        verdicts: dict[str, list[list[str]]] = {}
        for output in stage_result.filter_outputs:
            verdicts.setdefault(output.strategy_id, []).append(output.filter_result.failures)
        passed = {config.strategy_id for config in stage_result.survivors}

        for strategy_id in _named(stage_result) if entrants is None else entrants:
            record = records.setdefault(
                strategy_id,
                {
                    "strategy_id": strategy_id,
                    "passed_every_stage": False,
                    "eliminated_at": None,
                    "stages": [],
                },
            )
            entry: dict[str, Any] = {
                "stage": stage_result.stage_name,
                "passed": strategy_id in passed,
            }
            own = verdicts.get(strategy_id, [])
            if len(own) == 1:
                entry["failures"] = list(own[0])
            diagnostics = stage_result.diagnostics.get(strategy_id)
            if diagnostics is not None:
                entry.update(_evidence(diagnostics))
            record["stages"].append(entry)
            if strategy_id not in passed:
                record["eliminated_at"] = stage_result.stage_name
        entrants = [config.strategy_id for config in stage_result.survivors]

    # Whoever the last stage that ran let through. A pipeline that ran out of
    # survivors stops early, and its last stage let nobody through.
    for strategy_id in entrants or []:
        if strategy_id in records:
            records[strategy_id]["passed_every_stage"] = True

    report = {
        "stages": [stage_result.stage_name for stage_result in stage_results],
        "strategies": list(records.values()),
    }
    safe: dict[str, Any] = _json_safe(report)
    _log(safe)
    return safe


def gate_report_not_built(reason: str) -> dict[str, Any]:
    """The report of a tick whose stage results could not be written down."""
    return {"stages": [], "strategies": [], "not_built": reason}


def _named(stage_result: StageResult) -> list[str]:
    """The strategies a stage result names, in the order it names them."""
    named: dict[str, None] = {}
    for output in stage_result.filter_outputs:
        named.setdefault(output.strategy_id)
    for strategy_id in stage_result.diagnostics:
        named.setdefault(strategy_id)
    for config in stage_result.survivors:
        named.setdefault(config.strategy_id)
    return list(named)


# ---------------------------------------------------------------------------
# Evidence, one block per kind a stage records
# ---------------------------------------------------------------------------


def _evidence(diagnostics: StageDiagnostics) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if diagnostics.reference_result is not None:
        evidence["reference_run"] = _reference_run(diagnostics.reference_result)
    if diagnostics.fold_inputs:
        evidence["folds"] = [_fold(fold) for fold in diagnostics.fold_inputs]
        evidence["folds_passed"] = sum(1 for fold in diagnostics.fold_inputs if fold.fold_passed)
    if diagnostics.mc_aggregation is not None:
        evidence["monte_carlo"] = _monte_carlo(diagnostics.mc_aggregation)
    if diagnostics.regime_verdict is not None:
        evidence["regime"] = _regime(diagnostics.regime_verdict)
    if diagnostics.stress_verdict is not None:
        evidence["stress"] = _stress(diagnostics.stress_verdict)
    if diagnostics.overfitting_verdict is not None:
        evidence["overfitting"] = _overfitting(diagnostics.overfitting_verdict)
    return evidence


def _reference_run(result: SimulationRunResult) -> dict[str, Any]:
    """The full-window run a gate judged: its dates say which days the gate read."""
    return {
        "first": result.start_date.isoformat(),
        "last": result.end_date.isoformat(),
        "sharpe": result.risk_metrics.sharpe_ratio,
        "total_return": result.return_metrics.total_return,
        "max_drawdown": result.risk_metrics.max_drawdown,
        "trade_count": result.trade_metrics.total_trades,
    }


def _fold(fold: FoldValidationInput) -> dict[str, Any]:
    return {
        "fold": fold.fold_index,
        "passed": fold.fold_passed,
        "train_passed": fold.train_passed,
        "test_passed": fold.test_passed,
        "train_sharpe": fold.train_sharpe,
        "test_sharpe": fold.test_sharpe,
        "train_return": fold.train_return,
        "test_return": fold.test_return,
        "train_drawdown": fold.train_drawdown,
        "test_drawdown": fold.test_drawdown,
    }


def _monte_carlo(aggregation: MonteCarloAggregation) -> dict[str, Any]:
    return {
        "passed": aggregation.passed,
        "paths": aggregation.n_runs,
        "paths_passed": aggregation.n_passed,
        "pass_rate": aggregation.pass_rate,
        "prob_positive_sharpe": aggregation.prob_positive_sharpe,
        "prob_sharpe_above_min": aggregation.prob_sharpe_above_min,
        "sessions": aggregation.n_sessions,
        "sharpe": _distribution(aggregation.sharpe_dist),
        "total_return": _distribution(aggregation.return_dist),
        "max_drawdown": _distribution(aggregation.drawdown_dist),
        # The aggregation's own list: what decided the verdict first, then the share of
        # paths failing each threshold, which is listed for a strategy that passed too.
        "failure_reasons": list(aggregation.failure_reasons),
    }


def _distribution(dist: MetricDistribution) -> dict[str, Any]:
    return {
        "mean": dist.mean,
        "median": dist.median,
        "std": dist.std,
        "min": dist.min,
        "max": dist.max,
        "p05": dist.p05,
        "p95": dist.p95,
    }


def _regime(verdict: RegimeGateVerdict) -> dict[str, Any]:
    return {
        "passed": verdict.passed,
        "enforced": verdict.enforced,
        "findings": list(verdict.findings),
        "failure_reasons": list(verdict.failure_reasons),
        "warnings": list(verdict.warnings),
        "dimensions": [
            {
                "dimension": check.dimension,
                "evaluated": check.evaluated,
                "labels": list(check.evaluable_labels),
                "worst_label": check.worst_label,
                "worst_sharpe": check.worst_sharpe,
                "worst_drawdown": check.worst_drawdown,
                "positive_fraction": check.positive_fraction,
                "failures": list(check.failures),
            }
            for check in verdict.dimension_checks
        ],
    }


def _stress(verdict: StressGateVerdict) -> dict[str, Any]:
    shocks = verdict.shock_summary.scenario_results if verdict.shock_summary is not None else []
    return {
        "passed": verdict.passed,
        # The pass rule: the runs repeated at a multiple of the execution costs.
        "cost_survival_rate": verdict.cost_survival_rate,
        "costs": [
            {
                "cost_multiplier": cost.cost_multiplier,
                "sharpe": cost.sharpe,
                "total_return": cost.total_return,
                "max_drawdown": cost.max_drawdown,
                "trade_count": cost.trade_count,
            }
            for cost in verdict.cost_results
        ],
        # Report only: the shocks never decide the verdict.
        "shock_survival_rate": verdict.shock_survival_rate,
        "shocks": [
            {
                "scenario": shock.scenario_name,
                "survived": shock.survived,
                "sharpe": shock.stressed_sharpe,
                "total_return": shock.stressed_return,
                "max_drawdown": shock.stressed_drawdown,
                "sharpe_degradation": shock.sharpe_degradation,
            }
            for shock in shocks
        ],
        "failure_reasons": list(verdict.failure_reasons),
        "warnings": list(verdict.warnings),
    }


def _overfitting(verdict: OverfittingGateVerdict) -> dict[str, Any]:
    analysis = verdict.analysis
    indicators = analysis.indicators
    return {
        "passed": verdict.passed,
        "enforced": verdict.enforced,
        "overfitting_probability": analysis.overfitting_probability,
        "core_indicators": verdict.n_core_indicators,
        "indicators": {
            "train_test_degradation": indicators.train_test_degradation,
            "fold_instability": indicators.fold_instability,
            "mc_instability": indicators.mc_instability,
            "regime_concentration": indicators.regime_concentration,
            "low_trade_count_flag": indicators.low_trade_count_flag,
            "narrow_period_alpha": indicators.narrow_period_alpha,
            "parameter_fragility": indicators.parameter_fragility,
        },
        "most_suspicious": list(analysis.most_suspicious),
        "findings": list(verdict.findings),
        "failure_reasons": list(verdict.failure_reasons),
        "warnings": [*verdict.warnings, *analysis.warnings],
    }


# ---------------------------------------------------------------------------
# Writing it out: as JSON in the summary, and as lines in the log
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """``value`` as the summary can carry it. The summary is written out as JSON, which
    has no NaN or infinity (a Sharpe over a flat curve) and no numpy scalar: a number
    that is not finite becomes None."""
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _log(report: dict[str, Any]) -> None:
    """One line for the run, then one for each strategy that got past the first stage:
    the first stage's failures are in the report, and a large grid has many of them."""
    strategies = report["strategies"]
    logger.info(
        "gate_report | %d strategies | %d passed every stage",
        len(strategies),
        sum(1 for record in strategies if record["passed_every_stage"]),
    )
    for record in strategies:
        if len(record["stages"]) < 2:
            continue
        logger.info(
            "gate_report | %s | %s | %s",
            record["strategy_id"],
            "passed every stage"
            if record["passed_every_stage"]
            else f"eliminated at {record['eliminated_at']}",
            " | ".join(_brief(entry) for entry in record["stages"][1:]),
        )


def _brief(entry: dict[str, Any]) -> str:
    """A stage entry in a few words, for the log line."""
    said: list[str] = []
    if "folds" in entry:
        said.append(f"folds {entry['folds_passed']}/{len(entry['folds'])}")
    if "monte_carlo" in entry:
        monte_carlo = entry["monte_carlo"]
        said.append(
            f"paths {monte_carlo['paths_passed']}/{monte_carlo['paths']}, "
            f"P(Sharpe>0) {_shown(monte_carlo['prob_positive_sharpe'])}"
        )
    if "regime" in entry:
        said.append(_findings(entry["regime"]))
    if "stress" in entry:
        stress = entry["stress"]
        said.append(
            f"cost runs survived {_shown(stress['cost_survival_rate'])}, "
            f"shocks survived {_shown(stress['shock_survival_rate'])} (shocks report only)"
        )
    if "overfitting" in entry:
        overfitting = entry["overfitting"]
        said.append(
            f"probability {_shown(overfitting['overfitting_probability'])}, "
            f"{_findings(overfitting)}"
        )
    if not entry["passed"]:
        # Why, in the stage's own words; walk-forward's folds have already said it.
        said.extend(entry.get("failures", []))
    verdict = "passed" if entry["passed"] else "FAILED"
    return f"{entry['stage']}: {verdict}" + (f" ({'; '.join(said)})" if said else "")


def _findings(block: dict[str, Any]) -> str:
    count = len(block["findings"])
    return f"{count} finding(s)" + ("" if block["enforced"] else ", report only")


def _shown(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
