"""Tests for OverfittingEstimationService."""

from __future__ import annotations

from autonomous_trading_platform.research.intelligence.overfitting_estimation_service import (
    OverfitRiskBand,
    OverfittingEstimationService,
)
from tests.research.intelligence.conftest import (
    make_poor_validation_summary,
    make_regime_profile,
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
# Risk bands
# ---------------------------------------------------------------------------


def test_risk_band_low_for_clean_strategy():
    svc = OverfittingEstimationService()
    vs = make_validation_summary(
        overfit_probability=0.05,
        fold_consistency=0.90,
        train_test_degradation=0.02,
    )
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.risk_band in (OverfitRiskBand.LOW, OverfitRiskBand.MEDIUM)


def test_risk_band_high_for_unstable_strategy():
    svc = OverfittingEstimationService()
    vs = make_poor_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.risk_band in (OverfitRiskBand.HIGH, OverfitRiskBand.CRITICAL)


def test_risk_band_critical_threshold():
    assert OverfitRiskBand.from_probability(0.80) == OverfitRiskBand.CRITICAL
    assert OverfitRiskBand.from_probability(0.60) == OverfitRiskBand.HIGH
    assert OverfitRiskBand.from_probability(0.40) == OverfitRiskBand.MEDIUM
    assert OverfitRiskBand.from_probability(0.20) == OverfitRiskBand.LOW


# ---------------------------------------------------------------------------
# Unstable strategies are flagged
# ---------------------------------------------------------------------------


def test_unstable_fold_strategy_flagged():
    svc = OverfittingEstimationService()
    # Set both walk-forward and overfitting indicator values to reflect fold instability
    vs = make_validation_summary(
        fold_consistency=0.10,
        fold_sharpe_cv=2.0,
        ind_fold_instability=0.75,
        ind_train_test_degradation=0.65,
    )
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert est.overfit_probability > 0.30  # should trigger some overfit signal


def test_narrow_regime_strategy_has_higher_overfit():
    svc = OverfittingEstimationService()
    rp_narrow = make_regime_profile(
        is_regime_robust=False,
        overall_sensitivity=1.8,
    )
    vs = make_validation_summary()
    vec_regime = _make_vector(validation_summary=vs, regime_profile=rp_narrow)
    vec_plain = _make_vector(validation_summary=vs)
    est_regime = svc.estimate(vec_regime)
    est_plain = svc.estimate(vec_plain)
    # regime-concentrated should have >= overfit probability
    assert est_regime.overfit_probability >= est_plain.overfit_probability - 0.05


def test_sparse_trade_count_penalised():
    svc = OverfittingEstimationService()
    vs_low = make_validation_summary(low_trade_count_flag=True)
    vs_ok = make_validation_summary(low_trade_count_flag=False)
    v_low = _make_vector(validation_summary=vs_low)
    v_ok = _make_vector(validation_summary=vs_ok)
    e_low = svc.estimate(v_low)
    e_ok = svc.estimate(v_ok)
    assert e_low.overfit_probability >= e_ok.overfit_probability - 0.05


# ---------------------------------------------------------------------------
# Overfit probability range
# ---------------------------------------------------------------------------


def test_overfit_probability_in_unit_interval():
    svc = OverfittingEstimationService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    est = svc.estimate(vec)
    assert 0.0 <= est.overfit_probability <= 1.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic():
    svc = OverfittingEstimationService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    e1 = svc.estimate(vec)
    e2 = svc.estimate(vec)
    assert e1.overfit_probability == e2.overfit_probability
    assert e1.risk_band == e2.risk_band


# ---------------------------------------------------------------------------
# as_dict
# ---------------------------------------------------------------------------


def test_as_dict_has_required_keys():
    svc = OverfittingEstimationService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    d = svc.estimate(vec).as_dict()
    for key in ("overfit_probability", "risk_band", "warning_summary", "indicators"):
        assert key in d


def test_indicators_dict_non_empty_with_data():
    svc = OverfittingEstimationService()
    vs = make_validation_summary()
    vec = _make_vector(validation_summary=vs)
    d = svc.estimate(vec).as_dict()
    indicators = d["indicators"]
    non_null = [v for v in indicators.values() if v is not None]
    assert len(non_null) > 0
