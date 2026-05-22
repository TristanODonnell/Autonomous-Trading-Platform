"""
Validation orchestrator — top-level entry point for the validation framework.

The orchestrator accepts a ValidationRequest containing already-computed
simulation artifacts and runs each enabled validation stage in sequence.
It aggregates stage results into a unified ValidationSummary with a
RobustnessScore and overall pass/fail verdict.

This service does NOT run simulations directly.  All simulation outputs
(equity curve, metrics, fold data, MC aggregation, regime profile) must be
provided by the caller.  This keeps the orchestrator:

  - fast  (no re-simulation unless parameter sensitivity is enabled)
  - testable  (pure function over in-memory data)
  - composable  (each stage can be run independently)

Stage execution order
---------------------
1. Survivorship validation      (config / symbol membership check)
2. Walk-forward validation      (fold consistency and degradation)
3. Regime validation            (regime concentration and sensitivity)
4. Overfitting analysis         (composite heuristic indicators)
5. Stress testing               (equity-curve transformation scenarios)
6. Parameter sensitivity        (disabled by default — requires N sim runs)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

import pandas as pd

from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.metric_labels import observability_environment
from autonomous_trading_platform.observability.metrics import (
    research_robustness_score,
    research_validation_stage_duration,
    research_validation_stage_runs,
)
from autonomous_trading_platform.research.validation.overfitting_analysis import (
    OverfittingAnalyzer,
)
from autonomous_trading_platform.research.validation.parameter_sensitivity_analysis import (
    ParameterSensitivityAnalyzer,
    SensitivityProfile,
)
from autonomous_trading_platform.research.validation.robustness_score import (
    RobustnessScoreBuilder,
    RobustnessWeights,
)
from autonomous_trading_platform.research.validation.stress_test_service import (
    StressScenario,
    StressTestService,
)
from autonomous_trading_platform.research.validation.survivorship_validation import (
    SurvivorshipValidationService,
)
from autonomous_trading_platform.research.validation.validation_result import (
    ValidationStageResult,
    ValidationSummary,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
    WalkForwardValidationService,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationConfig:
    enable_walk_forward: bool = True
    enable_stress_tests: bool = True
    enable_survivorship_check: bool = True
    enable_overfitting_analysis: bool = True
    enable_parameter_sensitivity: bool = False  # needs N sim runs — off by default
    min_robustness_score: float = 0.5
    robustness_weights: RobustnessWeights | None = None
    min_stress_survival_rate: float = 0.5
    min_acceptable_sharpe: float = 0.0
    max_drawdown_threshold: float = -0.40
    stress_scenarios: list[StressScenario] | None = None


@dataclass
class ValidationRequest:
    """
    All data needed to validate a single strategy run.

    Required
    --------
    strategy_id, experiment_id, dataset_version
        Identity fields.
    equity_curve
        Simulation equity curve (timestamp, equity, …).

    Optional enrichment (enables richer analysis)
    -------------------
    wf_fold_inputs          List of per-fold train/test metric pairs.
    mc_aggregation          MonteCarloAggregation for MC instability indicator.
    regime_profile          StrategyRegimeProfile for regime validation.
    universe_scope          ExperimentUniverseScope for survivorship check.
    experiment_start/end    Required when universe_scope is provided.
    trade_count             Used by overfitting low-trade-count indicator.
    parameter_specs         Needed when enable_parameter_sensitivity is True.
    reference_parameters    Needed when enable_parameter_sensitivity is True.
    sensitivity_run_fn      Callable(params) -> (sharpe, drawdown, return).
    """

    strategy_id: str
    experiment_id: str
    dataset_version: str
    equity_curve: pd.DataFrame

    # Optional enrichment
    wf_fold_inputs: list[FoldValidationInput] | None = None
    mc_aggregation: Any | None = None  # MonteCarloAggregation
    regime_profile: Any | None = None  # StrategyRegimeProfile
    universe_scope: Any | None = None  # ExperimentUniverseScope
    experiment_start: date | None = None
    experiment_end: date | None = None
    trade_count: int | None = None
    parameter_specs: tuple | None = None  # tuple[ParameterSpec, ...]
    reference_parameters: dict[str, Any] | None = None
    sensitivity_run_fn: Callable | None = None


class ValidationOrchestrator:
    """
    Runs the full validation pipeline and returns a ValidationSummary.

    Parameters
    ----------
    walk_forward_service
        WalkForwardValidationService instance (created with defaults if None).
    stress_test_service
        StressTestService instance (created with defaults if None).
    survivorship_service
        SurvivorshipValidationService instance (created with no guard if None).
    overfitting_analyzer
        OverfittingAnalyzer instance (created with defaults if None).
    sensitivity_analyzer
        ParameterSensitivityAnalyzer instance (created with defaults if None).
    artifact_repository
        Optional ValidationArtifactRepository for persisting results.
    """

    def __init__(
        self,
        *,
        walk_forward_service: WalkForwardValidationService | None = None,
        stress_test_service: StressTestService | None = None,
        survivorship_service: SurvivorshipValidationService | None = None,
        overfitting_analyzer: OverfittingAnalyzer | None = None,
        sensitivity_analyzer: ParameterSensitivityAnalyzer | None = None,
        artifact_repository: Any | None = None,
    ) -> None:
        self._wf_service = walk_forward_service or WalkForwardValidationService()
        self._stress_service = stress_test_service or StressTestService()
        self._survivorship_service = survivorship_service or SurvivorshipValidationService()
        self._overfit_analyzer = overfitting_analyzer or OverfittingAnalyzer()
        self._sensitivity_analyzer = sensitivity_analyzer or ParameterSensitivityAnalyzer()
        self._repo = artifact_repository

    def run_validation(
        self,
        request: ValidationRequest,
        config: ValidationConfig | None = None,
    ) -> ValidationSummary:
        if config is None:
            config = ValidationConfig()
        validation_run_id = str(uuid4())
        stage_results: dict[str, ValidationStageResult] = {}
        score_builder = RobustnessScoreBuilder(weights=config.robustness_weights)
        sensitivity_profile: SensitivityProfile | None = None
        overfit_result = None
        _t0 = time.perf_counter()
        _env = observability_environment()
        _base_log_ctx = LogContext(
            run_id=validation_run_id,
            experiment_id=request.experiment_id,
            strategy_id=request.strategy_id,
            dataset_version=request.dataset_version,
            component="research.validation_orchestrator",
        )

        logger.info(
            "validation_started",
            extra=_base_log_ctx.to_extra(),
        )

        # ------------------------------------------------------------------
        # Stage 1: Survivorship validation
        # ------------------------------------------------------------------
        if config.enable_survivorship_check:
            _ts = time.perf_counter()
            surv_result = self._run_survivorship(request)
            _stage_dur = time.perf_counter() - _ts
            score = 1.0 if surv_result.is_safe else 0.0
            stage_results["survivorship"] = ValidationStageResult(
                stage_name="survivorship",
                passed=surv_result.is_safe,
                score=score,
                summary=surv_result.as_dict(),
                warnings=surv_result.warnings + surv_result.errors,
            )
            _vstatus = "passed" if surv_result.is_safe else "failed"
            research_validation_stage_runs.add(
                1, {"environment": _env, "stage_name": "survivorship", "status": _vstatus}
            )
            research_validation_stage_duration.record(
                _stage_dur, {"environment": _env, "stage_name": "survivorship"}
            )
            logger.info(
                "  survivorship: safe=%s errors=%d", surv_result.is_safe, len(surv_result.errors)
            )

        # ------------------------------------------------------------------
        # Stage 2: Walk-forward validation
        # ------------------------------------------------------------------
        if config.enable_walk_forward and request.wf_fold_inputs:
            _ts = time.perf_counter()
            wf_result = self._wf_service.analyze(request.wf_fold_inputs)
            _stage_dur = time.perf_counter() - _ts
            passed = wf_result.is_consistent
            stage_results["walk_forward"] = ValidationStageResult(
                stage_name="walk_forward",
                passed=passed,
                score=wf_result.fold_consistency,
                summary={
                    "n_folds": wf_result.n_folds,
                    "n_folds_passed": wf_result.n_folds_passed,
                    "fold_consistency": round(wf_result.fold_consistency, 4),
                    "avg_train_sharpe": round(wf_result.avg_train_sharpe, 4),
                    "avg_test_sharpe": round(wf_result.avg_test_sharpe, 4),
                    "train_test_degradation": round(wf_result.train_test_degradation, 4),
                    "fold_sharpe_cv": round(wf_result.fold_sharpe_cv, 4),
                },
                warnings=wf_result.warnings,
            )
            score_builder.with_walk_forward(
                folds_passed=wf_result.n_folds_passed,
                total_folds=wf_result.n_folds,
                train_test_degradation=wf_result.train_test_degradation,
            )
            _vstatus = "passed" if passed else "failed"
            research_validation_stage_runs.add(
                1, {"environment": _env, "stage_name": "walk_forward", "status": _vstatus}
            )
            research_validation_stage_duration.record(
                _stage_dur, {"environment": _env, "stage_name": "walk_forward"}
            )
            logger.info(
                "  walk_forward: %d/%d folds, degradation=%.3f",
                wf_result.n_folds_passed,
                wf_result.n_folds,
                wf_result.train_test_degradation,
            )

        # ------------------------------------------------------------------
        # Stage 3: Regime validation
        # ------------------------------------------------------------------
        if request.regime_profile is not None:
            _ts = time.perf_counter()
            regime_result = self._run_regime_validation(request.regime_profile)
            _stage_dur = time.perf_counter() - _ts
            stage_results["regime"] = ValidationStageResult(
                stage_name="regime",
                passed=regime_result["passed"],
                score=regime_result["score"],
                summary=regime_result["summary"],
                warnings=regime_result["warnings"],
            )
            score_builder.with_regime(
                is_regime_robust=request.regime_profile.is_regime_robust,
                worst_regime_sharpe=regime_result["worst_sharpe"],
                overall_sensitivity=request.regime_profile.overall_sensitivity,
            )
            _vstatus = "passed" if regime_result["passed"] else "failed"
            research_validation_stage_runs.add(
                1, {"environment": _env, "stage_name": "regime", "status": _vstatus}
            )
            research_validation_stage_duration.record(
                _stage_dur, {"environment": _env, "stage_name": "regime"}
            )

        # ------------------------------------------------------------------
        # Stage 4: Monte Carlo stability
        # ------------------------------------------------------------------
        if request.mc_aggregation is not None:
            mc_cv = request.mc_aggregation.sharpe_dist.coefficient_of_variation
            mc_passed = request.mc_aggregation.passed
            mc_score = max(0.0, 1.0 - mc_cv)
            stage_results["monte_carlo"] = ValidationStageResult(
                stage_name="monte_carlo",
                passed=mc_passed,
                score=min(1.0, mc_score),
                summary={
                    "n_runs": request.mc_aggregation.n_runs,
                    "n_passed": request.mc_aggregation.n_passed,
                    "pass_rate": round(request.mc_aggregation.pass_rate, 4),
                    "sharpe_mean": round(request.mc_aggregation.sharpe_dist.mean, 4),
                    "sharpe_cv": round(mc_cv, 4),
                },
                warnings=(
                    [f"Monte Carlo pass rate is {request.mc_aggregation.pass_rate:.0%}"]
                    if not mc_passed
                    else []
                ),
            )
            score_builder.with_monte_carlo(sharpe_cv=mc_cv)
            _vstatus = "passed" if mc_passed else "failed"
            research_validation_stage_runs.add(
                1, {"environment": _env, "stage_name": "monte_carlo", "status": _vstatus}
            )
            logger.info(
                "  monte_carlo: pass_rate=%.2f sharpe_cv=%.3f",
                request.mc_aggregation.pass_rate,
                mc_cv,
            )

        # ------------------------------------------------------------------
        # Stage 5: Overfitting analysis
        # ------------------------------------------------------------------
        if config.enable_overfitting_analysis:
            _ts = time.perf_counter()
            wf_for_overfit = (
                self._wf_service.analyze(request.wf_fold_inputs) if request.wf_fold_inputs else None
            )
            overfit_result = self._overfit_analyzer.analyze(
                strategy_id=request.strategy_id,
                wf_result=wf_for_overfit,
                mc_aggregation=request.mc_aggregation,
                regime_profile=request.regime_profile,
                trade_count=request.trade_count,
                equity_curve=request.equity_curve,
                sensitivity_score=None,  # updated after sensitivity stage
            )
            _stage_dur = time.perf_counter() - _ts
            _overfit_passed = overfit_result.overfitting_probability < 0.6
            stage_results["overfitting"] = ValidationStageResult(
                stage_name="overfitting",
                passed=_overfit_passed,
                score=overfit_result.resistance_score,
                summary=overfit_result.as_dict(),
                warnings=overfit_result.warnings,
            )
            score_builder.with_overfitting(
                overfitting_probability=overfit_result.overfitting_probability
            )
            _vstatus = "passed" if _overfit_passed else "failed"
            research_validation_stage_runs.add(
                1, {"environment": _env, "stage_name": "overfitting", "status": _vstatus}
            )
            research_validation_stage_duration.record(
                _stage_dur, {"environment": _env, "stage_name": "overfitting"}
            )
            logger.info(
                "  overfitting: P=%.3f resistance=%.3f",
                overfit_result.overfitting_probability,
                overfit_result.resistance_score,
            )

        # ------------------------------------------------------------------
        # Stage 6: Stress testing
        # ------------------------------------------------------------------
        if config.enable_stress_tests and not request.equity_curve.empty:
            _ts = time.perf_counter()
            stress_svc = self._stress_service
            if config.stress_scenarios:
                stress_svc = StressTestService(
                    min_sharpe_threshold=config.min_acceptable_sharpe,
                    max_drawdown_threshold=config.max_drawdown_threshold,
                    scenarios=config.stress_scenarios,
                )
            stress_summary = stress_svc.run(
                equity_curve=request.equity_curve,
                strategy_id=request.strategy_id,
            )
            _stage_dur = time.perf_counter() - _ts
            stress_passed = stress_summary.survival_rate >= config.min_stress_survival_rate
            stage_results["stress_test"] = ValidationStageResult(
                stage_name="stress_test",
                passed=stress_passed,
                score=stress_summary.survival_rate,
                summary={
                    "n_scenarios": stress_summary.n_scenarios,
                    "n_survived": stress_summary.n_survived,
                    "survival_rate": round(stress_summary.survival_rate, 4),
                    "avg_sharpe_degradation": round(stress_summary.avg_sharpe_degradation, 4),
                    "worst_scenario": stress_summary.worst_scenario,
                },
                warnings=stress_summary.warnings,
            )
            score_builder.with_stress(survival_rate=stress_summary.survival_rate)
            _vstatus = "passed" if stress_passed else "failed"
            research_validation_stage_runs.add(
                1, {"environment": _env, "stage_name": "stress_test", "status": _vstatus}
            )
            research_validation_stage_duration.record(
                _stage_dur, {"environment": _env, "stage_name": "stress_test"}
            )
            logger.info(
                "  stress_test: %d/%d survived (%.0f%%)",
                stress_summary.n_survived,
                stress_summary.n_scenarios,
                stress_summary.survival_rate * 100,
            )

        # ------------------------------------------------------------------
        # Stage 7: Parameter sensitivity (optional, expensive)
        # ------------------------------------------------------------------
        if (
            config.enable_parameter_sensitivity
            and request.parameter_specs
            and request.reference_parameters is not None
            and request.sensitivity_run_fn is not None
        ):
            _ts = time.perf_counter()
            sensitivity_profile = self._sensitivity_analyzer.build_profile(
                strategy_id=request.strategy_id,
                parameter_specs=request.parameter_specs,
                reference_parameters=request.reference_parameters,
                run_fn=request.sensitivity_run_fn,
            )
            _stage_dur = time.perf_counter() - _ts
            _sens_passed = sensitivity_profile.overall_stability_score >= 0.5
            stage_results["parameter_sensitivity"] = ValidationStageResult(
                stage_name="parameter_sensitivity",
                passed=_sens_passed,
                score=sensitivity_profile.overall_stability_score,
                summary=sensitivity_profile.as_dict(),
                warnings=sensitivity_profile.warnings,
            )
            score_builder.with_parameter_stability(
                overall_stability_score=sensitivity_profile.overall_stability_score
            )
            _vstatus = "passed" if _sens_passed else "failed"
            research_validation_stage_runs.add(
                1,
                {"environment": _env, "stage_name": "parameter_sensitivity", "status": _vstatus},
            )
            research_validation_stage_duration.record(
                _stage_dur, {"environment": _env, "stage_name": "parameter_sensitivity"}
            )
            logger.info(
                "  parameter_sensitivity: overall_stability=%.3f",
                sensitivity_profile.overall_stability_score,
            )

        # ------------------------------------------------------------------
        # Build robustness score and summary
        # ------------------------------------------------------------------
        robustness_score = score_builder.build()

        summary = ValidationSummary.create(
            validation_run_id=validation_run_id,
            experiment_id=request.experiment_id,
            strategy_id=request.strategy_id,
            dataset_version=request.dataset_version,
            stage_results=stage_results,
            robustness_score=robustness_score,
            min_robustness_score=config.min_robustness_score,
        )

        _total_dur = time.perf_counter() - _t0
        research_robustness_score.record(
            robustness_score.overall,
            {"environment": _env},
        )
        logger.info(
            "validation_completed",
            extra=LogContext(
                run_id=validation_run_id,
                experiment_id=request.experiment_id,
                strategy_id=request.strategy_id,
                component="research.validation_orchestrator",
                duration_seconds=_total_dur,
                robustness_score=robustness_score.overall,
            ).to_extra(),
        )

        if self._repo is not None:
            try:
                self._repo.persist(summary)
            except Exception as exc:
                logger.warning("Failed to persist validation artifacts: %s", exc)

        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_survivorship(self, request: ValidationRequest) -> Any:
        return self._survivorship_service.validate(
            universe_scope=request.universe_scope,
            experiment_start=request.experiment_start or date(2000, 1, 1),
            experiment_end=request.experiment_end or date(2100, 1, 1),
        )

    def _run_regime_validation(self, regime_profile: Any) -> dict:
        """Extract regime validation metrics from a StrategyRegimeProfile."""
        dims = {
            "trend": regime_profile.by_trend,
            "volatility": regime_profile.by_volatility,
            "liquidity": regime_profile.by_liquidity,
            "mean_reversion": regime_profile.by_mean_reversion,
            "risk": regime_profile.by_risk,
        }

        worst_sharpe = float("inf")
        regime_scores: dict[str, float] = {}

        for dim_name, dim_summary in dims.items():
            s = dim_summary.sensitivity
            regime_scores[dim_name] = s.regime_robustness
            if s.best_regime is not None and s.regime_robustness < worst_sharpe:
                worst_sharpe = s.regime_robustness

        if worst_sharpe == float("inf"):
            worst_sharpe = 0.0

        is_robust = regime_profile.is_regime_robust
        score = max(0.0, min(1.0, (worst_sharpe + 2.0) / 4.0))  # normalise [−2, 2] → [0, 1]

        warnings: list[str] = []
        if not is_robust:
            warnings.append(
                "Strategy is NOT regime-robust — performance is negative in some regimes."
            )
        if regime_profile.overall_sensitivity > 1.0:
            warnings.append(
                f"High regime sensitivity (std={regime_profile.overall_sensitivity:.3f}) — "
                "performance varies significantly across market conditions."
            )

        return {
            "passed": is_robust,
            "score": score,
            "worst_sharpe": worst_sharpe,
            "summary": {
                "is_regime_robust": is_robust,
                "overall_sensitivity": round(regime_profile.overall_sensitivity, 4),
                "worst_regime_sharpe": round(worst_sharpe, 4),
                "per_dimension": {k: round(v, 4) for k, v in regime_scores.items()},
            },
            "warnings": warnings,
        }
