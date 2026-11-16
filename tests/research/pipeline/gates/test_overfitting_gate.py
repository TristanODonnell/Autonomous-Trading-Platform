"""Unit tests for OverfittingGate evidence collection and elimination rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
    MetricDistribution,
    MonteCarloAggregation,
)
from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
    OverfittingEvidence,
    OverfittingGateConfig,
    collect_overfitting_evidence,
    evaluate_overfitting_gate,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import (
    StageDiagnostics,
    StageResult,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
)


def _fold(i: int, train: float, test: float) -> FoldValidationInput:
    return FoldValidationInput(
        fold_index=i,
        train_sharpe=train,
        test_sharpe=test,
        train_drawdown=-0.05,
        test_drawdown=-0.05,
        train_return=0.02,
        test_return=0.01,
        train_passed=True,
        test_passed=test > 0,
        fold_passed=test > 0,
    )


def _mc(prob_positive_sharpe: float) -> MonteCarloAggregation:
    """Only prob_positive_sharpe feeds the overfitting indicator; the rest is filler."""
    dist = MetricDistribution(mean=1.0, median=1.0, std=1.0, min=-2.0, max=4.0, p05=-0.6, p95=2.6)
    return MonteCarloAggregation(
        strategy_id="s1",
        n_runs=1000,
        n_passed=1000,
        pass_rate=1.0,
        passed=True,
        sharpe_dist=dist,
        return_dist=dist,
        drawdown_dist=dist,
        prob_positive_sharpe=prob_positive_sharpe,
        prob_sharpe_above_min=prob_positive_sharpe,
        n_sessions=63,
    )


def _ref(trade_count: int = 100):
    return SimpleNamespace(trade_count=trade_count, equity_curve=None)


# The rule tests run enforced: they are about what the rule finds and decides.
CFG = OverfittingGateConfig(enforce=True)
REPORT = OverfittingGateConfig()


def _two_folds(
    folds: tuple[tuple[float, float], tuple[float, float]], mc_positive: float
) -> OverfittingEvidence:
    """Two (train, test) Sharpe pairs, the most a 60-session window gives."""
    return OverfittingEvidence(
        fold_inputs=[_fold(i, train, test) for i, (train, test) in enumerate(folds)],
        mc_aggregation=_mc(mc_positive),
        reference_result=_ref(),  # type: ignore[arg-type]
    )


# Sharpes of the size seen on the 2026-11 real-bars runs (between -2 and 3).
STEADY_GOOD = _two_folds(((1.5, 1.0), (1.2, 0.8)), 0.80)
COLLAPSE = _two_folds(((2.9, 0.1), (2.5, -0.5)), 0.55)
STEADY_LOSER = _two_folds(((-1.0, -1.1), (-1.2, -1.3)), 0.20)
NEAR_ZERO_TRAIN = _two_folds(((0.15, -0.9), (0.05, -1.05)), 0.40)
TEST_BETTER = _two_folds(((0.5, 1.4), (0.3, 1.1)), 0.75)


class TestReportOnly:
    """Default mode: the score is computed and recorded, the strategy passes (plan item R14)."""

    def test_default_is_report_only(self) -> None:
        assert OverfittingGateConfig().enforce is False

    def test_a_finding_is_recorded_and_does_not_fail(self) -> None:
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=COLLAPSE, config=REPORT)
        assert verdict.passed
        assert verdict.enforced is False
        assert verdict.failure_reasons == ()
        assert "overfitting probability" in verdict.findings[0]

    def test_findings_are_the_same_in_both_modes(self) -> None:
        reported = evaluate_overfitting_gate(strategy_id="s1", evidence=COLLAPSE, config=REPORT)
        enforced = evaluate_overfitting_gate(strategy_id="s1", evidence=COLLAPSE, config=CFG)
        assert reported.findings == enforced.findings == enforced.failure_reasons
        assert reported.passed and not enforced.passed
        assert reported.analysis.overfitting_probability == pytest.approx(
            enforced.analysis.overfitting_probability
        )

    def test_clean_evidence_has_no_findings(self) -> None:
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=STEADY_GOOD, config=REPORT)
        assert verdict.passed and verdict.findings == ()

    def test_insufficient_evidence_set_to_fail_is_still_only_recorded(self) -> None:
        evidence = OverfittingEvidence(reference_result=_ref(trade_count=2))  # type: ignore[arg-type]
        verdict = evaluate_overfitting_gate(
            strategy_id="s1",
            evidence=evidence,
            config=OverfittingGateConfig(on_insufficient_evidence="fail"),
        )
        assert verdict.passed
        assert "insufficient overfitting evidence" in verdict.findings[0]


class TestTwoFoldsRealisticSharpes:
    """What the rule does with the evidence a 60-session window really produces.

    Two of these pin known weaknesses (the reason the gate is report-only), so a
    change to the score shows up here.
    """

    def test_steady_strategy_is_not_flagged(self) -> None:
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=STEADY_GOOD, config=CFG)
        assert verdict.passed
        assert verdict.analysis.overfitting_probability < 0.3

    def test_train_to_test_collapse_is_flagged(self) -> None:
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=COLLAPSE, config=CFG)
        assert not verdict.passed
        assert verdict.analysis.overfitting_probability > 0.6
        assert verdict.analysis.indicators.train_test_degradation == pytest.approx(0.54, abs=0.01)

    def test_test_better_than_train_has_no_drop(self) -> None:
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=TEST_BETTER, config=CFG)
        assert verdict.passed
        assert verdict.analysis.indicators.train_test_degradation == 0.0

    def test_known_weakness_a_steady_loser_is_not_flagged(self) -> None:
        # The score measures whether train and test agree, not whether the
        # strategy is good: losing equally in both reads as "not overfit".
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=STEADY_LOSER, config=CFG)
        assert verdict.passed
        assert verdict.analysis.overfitting_probability < 0.3

    def test_known_weakness_train_sharpe_near_zero_saturates_the_drop(self) -> None:
        # Drop = (train - test) / |train|. A train Sharpe of 0.1 in the
        # denominator sends it to its maximum whatever the strategy is.
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=NEAR_ZERO_TRAIN, config=CFG)
        assert verdict.analysis.indicators.train_test_degradation == 1.0


class TestCollectEvidence:
    def test_merges_across_stages_and_reindexes_folds(self) -> None:
        mc = _mc(0.95)
        ref = _ref()
        prior = [
            StageResult(stage_name="sim"),
            StageResult(
                stage_name="wf_a",
                diagnostics={"s1": StageDiagnostics(fold_inputs=[_fold(0, 1, 1), _fold(1, 1, 1)])},
            ),
            StageResult(
                stage_name="wf_b",
                diagnostics={"s1": StageDiagnostics(fold_inputs=[_fold(0, 1, 1)])},
            ),
            StageResult(
                stage_name="mc",
                diagnostics={"s1": StageDiagnostics(mc_aggregation=mc, reference_result=ref)},  # type: ignore[arg-type]
            ),
            StageResult(
                stage_name="other", diagnostics={"s2": StageDiagnostics(mc_aggregation=_mc(0.5))}
            ),
        ]
        evidence = collect_overfitting_evidence("s1", prior)
        assert [f.fold_index for f in evidence.fold_inputs] == [0, 1, 2]
        assert evidence.mc_aggregation is mc
        assert evidence.reference_result is ref
        assert evidence.regime_profile is None

    def test_no_evidence_for_unknown_strategy(self) -> None:
        evidence = collect_overfitting_evidence("nope", [StageResult(stage_name="x")])
        assert evidence.fold_inputs == []
        assert evidence.mc_aggregation is None


class TestOverfittingGate:
    def test_consistent_strategy_passes(self) -> None:
        evidence = OverfittingEvidence(
            fold_inputs=[_fold(0, 1.2, 1.1), _fold(1, 1.1, 1.0), _fold(2, 1.3, 1.2)],
            mc_aggregation=_mc(0.97),
            reference_result=_ref(),  # type: ignore[arg-type]
        )
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=CFG)
        assert verdict.passed
        assert verdict.n_core_indicators == 3
        assert verdict.analysis.overfitting_probability < 0.3

    def test_train_test_collapse_and_weak_mc_evidence_eliminate(self) -> None:
        evidence = OverfittingEvidence(
            fold_inputs=[_fold(0, 3.0, -0.5), _fold(1, 2.8, 0.2), _fold(2, 3.2, -1.0)],
            mc_aggregation=_mc(0.3),  # only 30% of resampled paths have a positive Sharpe
            reference_result=_ref(trade_count=5),  # type: ignore[arg-type]
        )
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=CFG)
        assert not verdict.passed
        assert "overfitting probability" in verdict.failure_reasons[0]
        assert verdict.analysis.indicators.train_test_degradation == pytest.approx(0.5, abs=0.1)
        assert verdict.analysis.indicators.mc_instability == pytest.approx(0.7)

    def test_weak_indicators_alone_are_insufficient_evidence(self) -> None:
        # Only low_trade_count is available → 0 core indicators → policy decides.
        evidence = OverfittingEvidence(reference_result=_ref(trade_count=2))  # type: ignore[arg-type]
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=CFG)
        assert verdict.passed
        assert verdict.n_core_indicators == 0
        assert "insufficient overfitting evidence" in verdict.warnings[0]

        strict = OverfittingGateConfig(on_insufficient_evidence="fail", enforce=True)
        assert not evaluate_overfitting_gate(
            strategy_id="s1", evidence=evidence, config=strict
        ).passed

    def test_threshold_is_configurable(self) -> None:
        evidence = OverfittingEvidence(
            fold_inputs=[_fold(0, 2.0, 1.0), _fold(1, 2.0, 0.8)],
            mc_aggregation=_mc(0.8),
        )
        prob = evaluate_overfitting_gate(
            strategy_id="s1", evidence=evidence, config=CFG
        ).analysis.overfitting_probability
        tight = OverfittingGateConfig(
            max_overfitting_probability=max(prob - 0.01, 0.01), enforce=True
        )
        loose = OverfittingGateConfig(
            max_overfitting_probability=min(prob + 0.01, 1.0), enforce=True
        )
        assert not evaluate_overfitting_gate(
            strategy_id="s1", evidence=evidence, config=tight
        ).passed
        assert evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=loose).passed

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"max_overfitting_probability": 0.0}, "max_overfitting_probability"),
            ({"min_core_indicators": 0}, "min_core_indicators"),
            ({"min_core_indicators": 5}, "min_core_indicators"),
            ({"on_insufficient_evidence": "maybe"}, "on_insufficient_evidence"),
        ],
    )
    def test_config_validation(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            OverfittingGateConfig(**kwargs)
