"""Tests for RobustnessPredictionService."""

from __future__ import annotations

from autonomous_trading_platform.research.intelligence.robustness_prediction_service import (
    DeploymentSuitability,
    RobustnessPredictionService,
)
from tests.research.intelligence.conftest import (
    make_poor_validation_summary,
    make_validation_summary,
)


def _make_vector(strategy_id: str = "s", validation_summary=None, regime_profile=None):
    from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
        ResearchFeatureVectorBuilder,
    )

    return ResearchFeatureVectorBuilder().build(
        strategy_id=strategy_id,
        experiment_id="exp",
        dataset_version="v1",
        validation_summary=validation_summary,
        regime_profile=regime_profile,
    )


# ---------------------------------------------------------------------------
# Robust strategies are rewarded
# ---------------------------------------------------------------------------


def test_robust_strategy_has_high_probability():
    svc = RobustnessPredictionService()
    vs = make_validation_summary(
        overall=0.85,
        walk_forward_consistency=0.90,
        stress_resilience=0.90,
    )
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.robustness_probability > 0.65


def test_fragile_strategy_has_low_probability():
    svc = RobustnessPredictionService()
    vs = make_poor_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.robustness_probability < 0.50


# ---------------------------------------------------------------------------
# Fragility scores
# ---------------------------------------------------------------------------


def test_robust_strategy_has_low_fragility():
    svc = RobustnessPredictionService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.fragility_score < 0.60


def test_fragile_strategy_has_high_fragility():
    svc = RobustnessPredictionService()
    vs = make_poor_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.fragility_score > 0.40


def test_fragility_plus_robustness_not_necessarily_one():
    # They are independently clamped, not strict complements
    svc = RobustnessPredictionService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert 0.0 <= est.robustness_probability <= 1.0
    assert 0.0 <= est.fragility_score <= 1.0


# ---------------------------------------------------------------------------
# Deployment suitability
# ---------------------------------------------------------------------------


def test_suitable_for_good_strategy():
    svc = RobustnessPredictionService(suitable_threshold=0.50)
    vs = make_validation_summary(overall=0.85, walk_forward_consistency=0.90)
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.deployment_suitability in (
        DeploymentSuitability.SUITABLE,
        DeploymentSuitability.BORDERLINE,
    )


def test_unsuitable_for_poor_strategy():
    svc = RobustnessPredictionService(borderline_threshold=0.55)
    vs = make_poor_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.deployment_suitability == DeploymentSuitability.UNSUITABLE


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_confidence_low_with_no_inputs():
    svc = RobustnessPredictionService()
    vec = _make_vector()
    est = svc.estimate(vec)
    assert est.confidence < 0.30


def test_confidence_high_with_rich_inputs():
    svc = RobustnessPredictionService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.confidence > 0.60


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic():
    svc = RobustnessPredictionService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    e1 = svc.estimate(vec)
    e2 = svc.estimate(vec)
    assert e1.robustness_probability == e2.robustness_probability
    assert e1.fragility_score == e2.fragility_score
    assert e1.deployment_suitability == e2.deployment_suitability


# ---------------------------------------------------------------------------
# as_dict
# ---------------------------------------------------------------------------


def test_as_dict_has_required_keys():
    svc = RobustnessPredictionService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    d = svc.estimate(vec).as_dict()
    for key in (
        "robustness_probability",
        "fragility_score",
        "confidence",
        "deployment_suitability",
    ):
        assert key in d


# ---------------------------------------------------------------------------
# Explanation
# ---------------------------------------------------------------------------


def test_explanation_is_non_empty_list():
    svc = RobustnessPredictionService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert isinstance(est.explanation, list)
    assert len(est.explanation) > 0
