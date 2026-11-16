"""Tests for RobustnessScoreBuilder and RobustnessScore."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.validation.evidence_policy import (
    MONTE_CARLO,
    REGIME,
    WALK_FORWARD,
    EvidencePolicy,
)
from autonomous_trading_platform.research.validation.robustness_score import (
    RobustnessScoreBuilder,
    RobustnessWeights,
)
from autonomous_trading_platform.research.validation.validation_result import ValidationSummary

# The weighting arithmetic in the first tests predates the evidence policy; they
# keep checking it with every component allowed to decide.
ANY_EVIDENCE = EvidencePolicy.unrestricted()


def test_empty_builder_returns_zero():
    score = RobustnessScoreBuilder(policy=ANY_EVIDENCE).build()
    assert score.overall == 0.0
    assert score.walk_forward_consistency is None
    assert score.explanation == []


def test_single_component_normalised():
    """With only walk_forward, effective weight should be 1.0 → overall == wf score."""
    score = (
        RobustnessScoreBuilder(policy=ANY_EVIDENCE)
        .with_walk_forward(folds_passed=10, total_folds=10)
        .build()
    )
    # No degradation → score = clamp(1.0 - 0.0) = 1.0
    assert score.overall == pytest.approx(1.0)
    assert score.walk_forward_consistency == pytest.approx(1.0)


def test_perfect_score_all_components():
    score = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=10, total_folds=10, train_test_degradation=0.0)
        .with_monte_carlo(prob_positive_sharpe=1.0)
        .with_regime(is_regime_robust=True, worst_regime_sharpe=2.0, overall_sensitivity=0.0)
        .with_parameter_stability(overall_stability_score=1.0)
        .with_stress(survival_rate=1.0)
        .with_overfitting(overfitting_probability=0.0)
        .build()
    )
    # Regime formula caps at 0.8 for worst_regime_sharpe=2.0, so overall is near but not at 1.0
    assert score.overall > 0.85
    assert score.walk_forward_consistency == pytest.approx(1.0)
    assert score.mc_stability == pytest.approx(1.0)


def test_poor_score_all_components():
    score = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=1, total_folds=10, train_test_degradation=2.0)
        .with_monte_carlo(prob_positive_sharpe=0.1)
        .with_regime(is_regime_robust=False, worst_regime_sharpe=-2.0, overall_sensitivity=5.0)
        .with_parameter_stability(overall_stability_score=0.0)
        .with_stress(survival_rate=0.0)
        .with_overfitting(overfitting_probability=1.0)
        .build()
    )
    assert score.overall < 0.3


def test_partial_components_weight_redistribution():
    """Score with 2 components should equal weighted average of just those 2."""
    weights = RobustnessWeights(
        walk_forward=0.30,
        mc_stability=0.20,
        regime=0.20,
        parameter_stability=0.15,
        stress_resilience=0.10,
        overfitting_resistance=0.05,
    )
    score = (
        RobustnessScoreBuilder(weights=weights, policy=ANY_EVIDENCE)
        .with_walk_forward(folds_passed=8, total_folds=10)
        .with_stress(survival_rate=0.6)
        .build()
    )
    # Effective weights: wf=0.30, stress=0.10, sum=0.40 → renorm: wf=0.75, stress=0.25
    wf_score = 8 / 10  # 0.8
    stress_score = 0.6
    expected = 0.75 * wf_score + 0.25 * stress_score
    assert score.overall == pytest.approx(expected, abs=0.01)


def test_negative_weights_raise():
    with pytest.raises(ValueError):
        RobustnessWeights(walk_forward=-0.1)


def test_robustness_score_clamp():
    """Even with extreme inputs, score stays in [0, 1]."""
    score = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=100, total_folds=1)
        .with_stress(survival_rate=2.0)
        .build()
    )
    assert 0.0 <= score.overall <= 1.0


def test_explanation_populated():
    score = (
        RobustnessScoreBuilder(policy=ANY_EVIDENCE)
        .with_walk_forward(folds_passed=7, total_folds=10)
        .with_stress(survival_rate=0.75)
        .build()
    )
    assert len(score.explanation) == 2
    assert any("walk_forward" in e for e in score.explanation)
    assert any("stress" in e for e in score.explanation)


def test_as_dict_structure():
    score = RobustnessScoreBuilder().with_walk_forward(folds_passed=5, total_folds=5).build()
    d = score.as_dict()
    assert "overall" in d
    assert "components" in d
    assert "weights_used" in d
    assert "explanation" in d
    assert 0.0 <= d["overall"] <= 1.0


def test_mc_stability_is_prob_positive_sharpe():
    """Stability is the share of resampled paths with a positive Sharpe, unchanged."""
    for prob in (0.0, 0.5, 0.83, 1.0):
        score = RobustnessScoreBuilder().with_monte_carlo(prob_positive_sharpe=prob).build()
        assert score.mc_stability == pytest.approx(prob)


def test_mc_stability_clamped_to_unit_interval():
    builder = RobustnessScoreBuilder()
    assert builder.with_monte_carlo(prob_positive_sharpe=1.2).build().mc_stability == 1.0
    assert builder.with_monte_carlo(prob_positive_sharpe=-0.1).build().mc_stability == 0.0


def test_deterministic():
    builder1 = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=7, total_folds=10)
        .with_monte_carlo(prob_positive_sharpe=0.8)
        .with_regime(is_regime_robust=True, worst_regime_sharpe=0.5)
    )
    builder2 = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=7, total_folds=10)
        .with_monte_carlo(prob_positive_sharpe=0.8)
        .with_regime(is_regime_robust=True, worst_regime_sharpe=0.5)
    )
    assert builder1.build().overall == pytest.approx(builder2.build().overall)


# ---------------------------------------------------------------------------
# Evidence policy (plan R5 / R16): the default lets only walk-forward and
# Monte Carlo decide, and requires both.
# ---------------------------------------------------------------------------


def _survivor(**overrides):
    """The real-bars survivor of 2026-11-15: 1 of 2 folds, P(Sharpe > 0) = 0.7225."""
    regime = {"is_regime_robust": False, "worst_regime_sharpe": -1.0}
    regime.update(overrides.pop("regime", {}))
    builder = (
        RobustnessScoreBuilder(policy=overrides.pop("policy", None))
        .with_walk_forward(folds_passed=1, total_folds=2, train_test_degradation=0.6544)
        .with_monte_carlo(prob_positive_sharpe=0.7225)
        .with_regime(**regime)
        .with_stress(survival_rate=overrides.pop("survival_rate", 2 / 7))
        .with_overfitting(overfitting_probability=overrides.pop("overfitting_probability", 0.43))
    )
    assert not overrides
    return builder.build()


class TestDefaultEvidencePolicy:
    def test_only_walk_forward_and_monte_carlo_carry_weight(self) -> None:
        score = _survivor()
        assert score.evidence_complete and score.missing_evidence == ()
        assert score.weights_used == pytest.approx(
            {
                "walk_forward_consistency": 0.6,
                "mc_stability": 0.4,
                "regime_robustness": 0.0,
                "stress_resilience": 0.0,
                "overfitting_resistance": 0.0,
            }
        )
        expected = 0.6 * score.walk_forward_consistency + 0.4 * score.mc_stability
        assert score.overall == pytest.approx(expected)
        assert score.overall == pytest.approx(0.393, abs=0.001)

    def test_report_only_components_are_still_recorded(self) -> None:
        score = _survivor()
        assert score.regime_robustness is not None
        assert score.stress_resilience == pytest.approx(2 / 7)
        assert score.overfitting_resistance == pytest.approx(0.57)
        assert any(
            e.startswith("reported with weight 0: regime_robustness") for e in score.explanation
        )

    def test_report_only_components_cannot_move_the_score(self) -> None:
        base = _survivor().overall
        best = _survivor(
            regime={"is_regime_robust": True, "worst_regime_sharpe": 3.0},
            survival_rate=1.0,
            overfitting_probability=0.0,
        )
        worst = _survivor(survival_rate=0.0, overfitting_probability=1.0)
        assert best.overall == pytest.approx(base)
        assert worst.overall == pytest.approx(base)

    @pytest.mark.parametrize(
        ("build", "missing"),
        [
            (lambda b: b.with_walk_forward(folds_passed=2, total_folds=2), (MONTE_CARLO,)),
            (lambda b: b.with_monte_carlo(prob_positive_sharpe=1.0), (WALK_FORWARD,)),
            (
                lambda b: b.with_overfitting(overfitting_probability=0.45),
                (WALK_FORWARD, MONTE_CARLO),
            ),
            (lambda b: b, (WALK_FORWARD, MONTE_CARLO)),
        ],
    )
    def test_missing_required_evidence_means_no_score(self, build, missing) -> None:
        score = build(RobustnessScoreBuilder()).build()
        assert score.overall == 0.0
        assert not score.evidence_complete
        assert score.missing_evidence == missing
        assert score.weights_used == {}
        assert any(e.startswith("not scored: required evidence missing") for e in score.explanation)
        assert score.as_dict()["evidence_complete"] is False

    def test_less_evidence_never_scores_higher_than_more(self) -> None:
        # Measured 2026-11-15: strategies with only the overfitting component scored
        # about 0.36 and two that never traded 0.55, above the real survivor's 0.34.
        only_overfitting = RobustnessScoreBuilder().with_overfitting(overfitting_probability=0.45)
        assert only_overfitting.build().overall == 0.0 < _survivor().overall

    def test_an_unscored_summary_does_not_pass(self) -> None:
        score = RobustnessScoreBuilder().with_walk_forward(folds_passed=2, total_folds=2).build()
        summary = ValidationSummary.create(
            validation_run_id="v",
            experiment_id="e",
            strategy_id="s",
            dataset_version="d",
            stage_results={},
            robustness_score=score,
            min_robustness_score=0.0,
        )
        assert summary.overall_passed is False
        assert any("required evidence missing (mc_stability)" in w for w in summary.warnings)


class TestEvidencePolicy:
    def test_switching_a_gate_back_on_gives_its_component_weight_again(self) -> None:
        policy = EvidencePolicy(deciding=frozenset({WALK_FORWARD, MONTE_CARLO, REGIME}))
        score = _survivor(policy=policy)
        assert score.weights_used["regime_robustness"] == pytest.approx(0.20 / 0.70)
        assert score.weights_used["stress_resilience"] == 0.0
        assert score.overall != pytest.approx(_survivor().overall)

    def test_required_components_must_be_deciding(self) -> None:
        with pytest.raises(ValueError, match="must also be deciding"):
            EvidencePolicy(deciding=frozenset({WALK_FORWARD}), required=frozenset({MONTE_CARLO}))

    def test_unknown_component_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown component"):
            EvidencePolicy(deciding=frozenset({"sharpe"}), required=frozenset())
