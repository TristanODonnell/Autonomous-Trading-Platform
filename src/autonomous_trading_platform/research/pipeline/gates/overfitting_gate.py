"""
OverfittingGate — formal elimination on evidence gathered by earlier stages.

Walk-forward, Monte Carlo and regime stages each look at one symptom in
isolation. This gate combines them through the existing OverfittingAnalyzer
(TASK-2.4) into a single overfitting probability and eliminates strategies
above max_overfitting_probability.

Evidence used (whatever earlier stages produced):
  fold_inputs      -> train/test degradation, fold instability
  mc_aggregation   -> Monte Carlo P(Sharpe > 0) across resampled paths
  regime_profile   -> regime concentration
  reference_result -> trade count, narrow-period alpha (equity curve)

low_trade_count and narrow_period_alpha are always available and weakly
weighted; on their own they would decide the probability. So the evidence
threshold counts only the core cross-stage indicators (CORE_INDICATORS): below
min_core_indicators the on_insufficient_evidence policy decides.

Report-only by default (enforce=False). The score is computed and what the rule
found is recorded on the verdict as ``findings``, but the strategy passes.
Reason (plan item R14): on the 2026-11 real-bars runs the gate changed no
outcome. Sent all 30 strategies, it passed 26, among them 16 of the 20 losing
ones, and the four it failed had already failed earlier stages. The score
measures whether train and test agree, not whether a strategy is good, and half
its weight sits on two ratios of fold Sharpes (the train-to-test drop and the
spread across folds) that are unstable with the two folds a 60-session window
gives. Set enforce=True only once the walk-forward evidence has enough folds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from autonomous_trading_platform.research.validation.overfitting_analysis import (
    OverfittingAnalysisResult,
    OverfittingAnalyzer,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
    WalkForwardValidationService,
)

if TYPE_CHECKING:
    from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
        StrategyRegimeProfile,
    )
    from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
        MonteCarloAggregation,
    )
    from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
    from autonomous_trading_platform.research.simulation.simulation_runner import (
        SimulationRunResult,
    )

CORE_INDICATORS: frozenset[str] = frozenset(
    {"train_test_degradation", "fold_instability", "mc_instability", "regime_concentration"}
)


@dataclass(frozen=True)
class OverfittingGateConfig:
    max_overfitting_probability: float = 0.6
    min_core_indicators: int = 2
    min_trade_count: int = 30
    on_insufficient_evidence: Literal["pass", "fail"] = "pass"
    indicator_weights: dict[str, float] | None = None
    # False = report only: findings are recorded, the strategy passes (see module docstring).
    enforce: bool = False

    def __post_init__(self) -> None:
        if not (0.0 < self.max_overfitting_probability <= 1.0):
            raise ValueError("max_overfitting_probability must be in (0, 1]")
        if not (1 <= self.min_core_indicators <= len(CORE_INDICATORS)):
            raise ValueError(f"min_core_indicators must be in [1, {len(CORE_INDICATORS)}]")
        if self.min_trade_count < 0:
            raise ValueError("min_trade_count must be >= 0")
        if self.on_insufficient_evidence not in ("pass", "fail"):
            raise ValueError("on_insufficient_evidence must be 'pass' or 'fail'")


@dataclass
class OverfittingEvidence:
    fold_inputs: list[FoldValidationInput] = field(default_factory=list)
    mc_aggregation: MonteCarloAggregation | None = None
    regime_profile: StrategyRegimeProfile | None = None
    reference_result: SimulationRunResult | None = None


@dataclass(frozen=True)
class OverfittingGateVerdict:
    """Gate decision.

    ``findings`` is what the rule found, enforced or not. ``failure_reasons``
    is the subset that eliminated the strategy: equal to ``findings`` when
    ``enforced``, empty otherwise (``passed`` is then always True).
    """

    strategy_id: str
    passed: bool
    analysis: OverfittingAnalysisResult
    n_core_indicators: int
    failure_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    enforced: bool = False


def collect_overfitting_evidence(
    strategy_id: str,
    prior_results: Sequence[StageResult],
) -> OverfittingEvidence:
    """Gather the latest evidence for one strategy across earlier stages.

    Fold inputs are concatenated across walk-forward stages (re-indexed so
    fold indices stay unique); everything else takes the most recent stage
    that produced it.
    """
    evidence = OverfittingEvidence()
    for stage_result in prior_results:
        diag = stage_result.diagnostics.get(strategy_id)
        if diag is None:
            continue
        if diag.fold_inputs:
            offset = len(evidence.fold_inputs)
            evidence.fold_inputs.extend(
                _reindex(f, offset + i) for i, f in enumerate(diag.fold_inputs)
            )
        if diag.mc_aggregation is not None:
            evidence.mc_aggregation = diag.mc_aggregation
        if diag.regime_profile is not None:
            evidence.regime_profile = diag.regime_profile
        if diag.reference_result is not None:
            evidence.reference_result = diag.reference_result
    return evidence


def _reindex(fold: FoldValidationInput, index: int) -> FoldValidationInput:
    if fold.fold_index == index:
        return fold
    return FoldValidationInput(
        fold_index=index,
        train_sharpe=fold.train_sharpe,
        test_sharpe=fold.test_sharpe,
        train_drawdown=fold.train_drawdown,
        test_drawdown=fold.test_drawdown,
        train_return=fold.train_return,
        test_return=fold.test_return,
        train_passed=fold.train_passed,
        test_passed=fold.test_passed,
        fold_passed=fold.fold_passed,
    )


def evaluate_overfitting_gate(
    *,
    strategy_id: str,
    evidence: OverfittingEvidence,
    config: OverfittingGateConfig,
) -> OverfittingGateVerdict:
    analyzer = OverfittingAnalyzer(
        min_trade_count=config.min_trade_count,
        indicator_weights=config.indicator_weights,
    )
    wf_result = (
        WalkForwardValidationService().analyze(evidence.fold_inputs)
        if evidence.fold_inputs
        else None
    )
    ref = evidence.reference_result
    analysis = analyzer.analyze(
        strategy_id=strategy_id,
        wf_result=wf_result,
        mc_aggregation=evidence.mc_aggregation,
        regime_profile=evidence.regime_profile,
        trade_count=ref.trade_count if ref is not None else None,
        equity_curve=ref.equity_curve if ref is not None else None,
    )
    n_core = len(CORE_INDICATORS & analysis.indicators.active().keys())

    if n_core < config.min_core_indicators:
        message = (
            f"insufficient overfitting evidence: {n_core} core indicator(s) "
            f"< {config.min_core_indicators}"
        )
        if config.on_insufficient_evidence == "pass":
            return _verdict(strategy_id, analysis, n_core, config, warnings=(message,))
        return _verdict(strategy_id, analysis, n_core, config, findings=(message,))

    prob = analysis.overfitting_probability
    if prob > config.max_overfitting_probability:
        return _verdict(
            strategy_id,
            analysis,
            n_core,
            config,
            findings=(
                f"overfitting probability {prob:.2f} > {config.max_overfitting_probability:.2f} "
                f"(top signals: {', '.join(analysis.most_suspicious)})",
            ),
        )
    return _verdict(strategy_id, analysis, n_core, config, warnings=tuple(analysis.warnings))


def _verdict(
    strategy_id: str,
    analysis: OverfittingAnalysisResult,
    n_core: int,
    config: OverfittingGateConfig,
    *,
    findings: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> OverfittingGateVerdict:
    """Findings eliminate the strategy only when the gate is enforced."""
    return OverfittingGateVerdict(
        strategy_id=strategy_id,
        passed=not findings if config.enforce else True,
        analysis=analysis,
        n_core_indicators=n_core,
        failure_reasons=findings if config.enforce else (),
        warnings=warnings,
        findings=findings,
        enforced=config.enforce,
    )
