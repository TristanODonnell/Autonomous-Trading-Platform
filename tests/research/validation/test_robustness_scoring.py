"""Tests for RobustnessScoreBuilder and RobustnessScore."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.validation.robustness_score import (
    RobustnessScoreBuilder,
    RobustnessWeights,
)


def test_empty_builder_returns_zero():
    score = RobustnessScoreBuilder().build()
    assert score.overall == 0.0
    assert score.walk_forward_consistency is None
    assert score.explanation == []


def test_single_component_normalised():
    """With only walk_forward, effective weight should be 1.0 → overall == wf score."""
    score = RobustnessScoreBuilder().with_walk_forward(folds_passed=10, total_folds=10).build()
    # No degradation → score = clamp(1.0 - 0.0) = 1.0
    assert score.overall == pytest.approx(1.0)
    assert score.walk_forward_consistency == pytest.approx(1.0)


def test_perfect_score_all_components():
    score = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=10, total_folds=10, train_test_degradation=0.0)
        .with_monte_carlo(sharpe_cv=0.0)
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
        .with_monte_carlo(sharpe_cv=5.0)
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
        RobustnessScoreBuilder(weights=weights)
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
        RobustnessScoreBuilder()
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


def test_mc_stability_cv_zero():
    """CV=0 → stability=1.0."""
    score = RobustnessScoreBuilder().with_monte_carlo(sharpe_cv=0.0).build()
    assert score.mc_stability == pytest.approx(1.0)


def test_mc_stability_cv_large():
    """Very large CV → stability approaches 0."""
    score = RobustnessScoreBuilder().with_monte_carlo(sharpe_cv=100.0).build()
    assert score.mc_stability < 0.05


def test_deterministic():
    builder1 = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=7, total_folds=10)
        .with_monte_carlo(sharpe_cv=0.3)
        .with_regime(is_regime_robust=True, worst_regime_sharpe=0.5)
    )
    builder2 = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=7, total_folds=10)
        .with_monte_carlo(sharpe_cv=0.3)
        .with_regime(is_regime_robust=True, worst_regime_sharpe=0.5)
    )
    assert builder1.build().overall == pytest.approx(builder2.build().overall)
