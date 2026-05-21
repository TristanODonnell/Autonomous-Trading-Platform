"""Tests for ResearchFeatureVectorBuilder and ResearchFeatureVector."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    _NEUTRAL,
    FIELD_NAMES,
    ResearchFeatureVectorBuilder,
)
from tests.research.intelligence.conftest import (
    make_regime_profile,
    make_validation_summary,
)

# ---------------------------------------------------------------------------
# Stable ordering
# ---------------------------------------------------------------------------


def test_field_ordering_is_stable():
    builder = ResearchFeatureVectorBuilder()
    v1 = builder.build(strategy_id="s1", experiment_id="e", dataset_version="v1")
    v2 = builder.build(strategy_id="s2", experiment_id="e", dataset_version="v1")
    assert v1.field_names == v2.field_names


def test_field_names_match_canonical_ordering():
    builder = ResearchFeatureVectorBuilder()
    v = builder.build(strategy_id="s", experiment_id="e", dataset_version="v1")
    assert v.field_names == FIELD_NAMES


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_all_values_in_unit_interval():
    builder = ResearchFeatureVectorBuilder()
    vs = make_validation_summary()
    rp = make_regime_profile()
    vec = builder.build(
        strategy_id="s",
        experiment_id="e",
        dataset_version="v1",
        validation_summary=vs,
        regime_profile=rp,
        strategy_family="momentum",
        n_parameters=5,
    )
    for f in vec.fields:
        assert 0.0 <= f.value <= 1.0, f"Field {f.name}={f.value} out of [0,1]"


def test_neutral_defaults_when_no_inputs():
    builder = ResearchFeatureVectorBuilder()
    vec = builder.build(strategy_id="s", experiment_id="e", dataset_version="v1")
    # Non-metadata fields without data should default to _NEUTRAL (0.5)
    # Family one-hot fields default to 0.0 (semantically: "not this family"), not _NEUTRAL
    family_field_names = {
        f"family_{n}" for n in ("momentum", "mean_reversion", "trend", "factor", "composite")
    }
    for f in vec.fields:
        if not f.has_data and f.name not in family_field_names:
            assert f.value == pytest.approx(_NEUTRAL), (
                f"Field {f.name} expected {_NEUTRAL}, got {f.value}"
            )


def test_sharpe_normalisation_zero_maps_to_neutral():
    from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
        _normalise_sharpe,
    )

    assert _normalise_sharpe(0.0) == pytest.approx(0.5)


def test_sharpe_normalisation_positive_above_neutral():
    from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
        _normalise_sharpe,
    )

    assert _normalise_sharpe(1.0) > 0.5
    assert _normalise_sharpe(3.0) == pytest.approx(1.0)


def test_sharpe_normalisation_negative_below_neutral():
    from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
        _normalise_sharpe,
    )

    assert _normalise_sharpe(-1.0) < 0.5
    assert _normalise_sharpe(-3.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Data completeness
# ---------------------------------------------------------------------------


def test_data_completeness_zero_without_inputs():
    builder = ResearchFeatureVectorBuilder()
    vec = builder.build(strategy_id="s", experiment_id="e", dataset_version="v1")
    # Metadata fields are also unknown → completeness should be very low
    assert vec.data_completeness < 0.20


def test_data_completeness_high_with_all_inputs():
    builder = ResearchFeatureVectorBuilder()
    vs = make_validation_summary()
    rp = make_regime_profile()
    vec = builder.build(
        strategy_id="s",
        experiment_id="e",
        dataset_version="v1",
        validation_summary=vs,
        regime_profile=rp,
        strategy_family="momentum",
        n_parameters=3,
    )
    assert vec.data_completeness > 0.80


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_same_inputs_same_hash():
    builder = ResearchFeatureVectorBuilder()
    vs = make_validation_summary()
    kwargs = dict(
        strategy_id="s",
        experiment_id="e",
        dataset_version="v1",
        validation_summary=vs,
    )
    v1 = builder.build(**kwargs)
    v2 = builder.build(**kwargs)
    assert v1.config_hash == v2.config_hash


def test_different_inputs_produce_different_hash():
    builder = ResearchFeatureVectorBuilder()
    v1 = builder.build(strategy_id="s1", experiment_id="e", dataset_version="v1")
    v2 = builder.build(strategy_id="s2", experiment_id="e", dataset_version="v1")
    assert v1.config_hash != v2.config_hash


# ---------------------------------------------------------------------------
# Strategy family one-hot
# ---------------------------------------------------------------------------


def test_family_one_hot_exclusive():
    builder = ResearchFeatureVectorBuilder()
    for fam in ("momentum", "mean_reversion", "trend", "factor", "composite"):
        vec = builder.build(
            strategy_id="s",
            experiment_id="e",
            dataset_version="v1",
            strategy_family=fam,
        )
        fv = vec.as_dict()
        family_fields = [k for k in fv if k.startswith("family_")]
        active = [k for k in family_fields if fv[k] >= 0.5]
        assert len(active) == 1, f"Expected 1 active family field for {fam}, got {active}"
        assert f"family_{fam}" in active


def test_unknown_family_all_zeros():
    builder = ResearchFeatureVectorBuilder()
    vec = builder.build(
        strategy_id="s",
        experiment_id="e",
        dataset_version="v1",
        strategy_family="unknown_family",
    )
    fv = vec.as_dict()
    family_vals = [fv[k] for k in fv if k.startswith("family_")]
    assert all(v < 0.5 for v in family_vals)


# ---------------------------------------------------------------------------
# Regime fields
# ---------------------------------------------------------------------------


def test_regime_is_robust_maps_to_one_when_robust():
    builder = ResearchFeatureVectorBuilder()
    rp = make_regime_profile(is_regime_robust=True)
    vec = builder.build(
        strategy_id="s",
        experiment_id="e",
        dataset_version="v1",
        regime_profile=rp,
    )
    assert vec.get("regime_is_robust") == pytest.approx(1.0)


def test_regime_is_robust_maps_to_zero_when_not():
    builder = ResearchFeatureVectorBuilder()
    rp = make_regime_profile(is_regime_robust=False)
    vec = builder.build(
        strategy_id="s",
        experiment_id="e",
        dataset_version="v1",
        regime_profile=rp,
    )
    assert vec.get("regime_is_robust") == pytest.approx(0.0)


def test_regime_sensitivity_inverted():
    builder = ResearchFeatureVectorBuilder()
    # Low sensitivity → high normalised score
    rp_low = make_regime_profile(overall_sensitivity=0.0)
    rp_high = make_regime_profile(overall_sensitivity=1.5)
    v_low = builder.build(
        strategy_id="s", experiment_id="e", dataset_version="v1", regime_profile=rp_low
    )
    v_high = builder.build(
        strategy_id="s", experiment_id="e", dataset_version="v1", regime_profile=rp_high
    )
    assert v_low.get("regime_sensitivity_norm") > v_high.get("regime_sensitivity_norm")


# ---------------------------------------------------------------------------
# as_array / as_dict consistency
# ---------------------------------------------------------------------------


def test_as_array_length_matches_field_count():
    builder = ResearchFeatureVectorBuilder()
    vec = builder.build(strategy_id="s", experiment_id="e", dataset_version="v1")
    assert len(vec.as_array) == len(vec.fields)


def test_as_dict_matches_as_array():
    builder = ResearchFeatureVectorBuilder()
    vs = make_validation_summary()
    vec = builder.build(
        strategy_id="s", experiment_id="e", dataset_version="v1", validation_summary=vs
    )
    d = vec.as_dict()
    for f, val in zip(vec.fields, vec.as_array, strict=False):
        assert d[f.name] == pytest.approx(val, abs=1e-5)


# ---------------------------------------------------------------------------
# Serialisation stability (values are rounded to 6dp in as_dict)
# ---------------------------------------------------------------------------


def test_serialisation_stable():
    builder = ResearchFeatureVectorBuilder()
    vs = make_validation_summary()
    vec = builder.build(
        strategy_id="s", experiment_id="e", dataset_version="v1", validation_summary=vs
    )
    d1 = vec.as_dict()
    d2 = vec.as_dict()
    assert d1 == d2
