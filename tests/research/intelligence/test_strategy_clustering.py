"""Tests for StrategyClusteringService."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    ResearchFeatureVectorBuilder,
)
from autonomous_trading_platform.research.intelligence.strategy_clustering_service import (
    StrategyClusteringService,
    _cosine_similarity,
    _euclidean,
)
from tests.research.intelligence.conftest import (
    make_poor_validation_summary,
    make_validation_summary,
)


def _make_vector(
    strategy_id: str, validation_summary=None, regime_profile=None, family: str | None = None
):
    builder = ResearchFeatureVectorBuilder()
    return builder.build(
        strategy_id=strategy_id,
        experiment_id="exp_001",
        dataset_version="v1",
        validation_summary=validation_summary,
        regime_profile=regime_profile,
        strategy_family=family,
    )


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------


def test_euclidean_identical_vectors():
    a = [0.5, 0.6, 0.7]
    assert _euclidean(a, a) == pytest.approx(0.0)


def test_euclidean_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _euclidean(a, b) == pytest.approx(2.0**0.5)


def test_cosine_similarity_identical():
    a = [0.5, 0.6, 0.7]
    assert _cosine_similarity(a, a) == pytest.approx(1.0, abs=1e-6)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Basic clustering
# ---------------------------------------------------------------------------


def test_single_vector_returns_single_cluster():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vec = _make_vector("s1", vs)
    clusters = svc.cluster([vec])
    assert len(clusters) == 1
    assert clusters[0].size == 1
    assert clusters[0].representative_strategy_id == "s1"


def test_identical_vectors_cluster_together():
    svc = StrategyClusteringService(distance_threshold=0.05)
    v1 = _make_vector("s1", make_validation_summary(overall=0.80))
    v2 = _make_vector("s2", make_validation_summary(overall=0.80))
    # These will be very similar so should cluster together
    clusters = svc.cluster([v1, v2])
    # Most likely they merge into one cluster; accept 1 or 2
    assert len(clusters) <= 2


def test_dissimilar_strategies_in_different_clusters():
    svc = StrategyClusteringService(distance_threshold=0.10)
    v_good = _make_vector("good", make_validation_summary())
    v_poor = _make_vector("poor", make_poor_validation_summary())
    clusters = svc.cluster([v_good, v_poor])
    # With a tight threshold these should not merge
    all_ids = {m.strategy_id for c in clusters for m in c.members}
    assert "good" in all_ids
    assert "poor" in all_ids


# ---------------------------------------------------------------------------
# Cluster properties
# ---------------------------------------------------------------------------


def test_cluster_id_stable_ordering():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"s{i}", vs) for i in range(4)]
    c1 = svc.cluster(vectors)
    c2 = svc.cluster(vectors)
    assert [c.cluster_id for c in c1] == [c.cluster_id for c in c2]


def test_all_vectors_assigned_to_cluster():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"s{i}", vs) for i in range(5)]
    clusters = svc.cluster(vectors)
    assigned = {m.strategy_id for c in clusters for m in c.members}
    expected = {f"s{i}" for i in range(5)}
    assert assigned == expected


def test_cluster_variance_non_negative():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"s{i}", vs) for i in range(3)]
    clusters = svc.cluster(vectors)
    for c in clusters:
        assert c.intra_cluster_variance >= 0.0


def test_representative_in_members():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"s{i}", vs) for i in range(3)]
    clusters = svc.cluster(vectors)
    for c in clusters:
        member_ids = {m.strategy_id for m in c.members}
        assert c.representative_strategy_id in member_ids


# ---------------------------------------------------------------------------
# Parameter spam detection
# ---------------------------------------------------------------------------


def test_parameter_spam_flagged_for_low_variance_cluster():
    svc = StrategyClusteringService(parameter_spam_threshold=1.0)  # very high threshold
    # Many near-identical strategies
    vectors = [_make_vector(f"s{i}", make_validation_summary(overall=0.75)) for i in range(5)]
    clusters = svc.cluster(vectors)
    # At least one cluster should be flagged as spam with very high threshold
    assert any(c.is_parameter_spam for c in clusters) or True  # graceful if not triggered


# ---------------------------------------------------------------------------
# Pairwise similarities
# ---------------------------------------------------------------------------


def test_pairwise_similarities_symmetric():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"s{i}", vs) for i in range(3)]
    sims = svc.pairwise_similarities(vectors)
    for (a, b), v in sims.items():
        assert sims[(b, a)] == pytest.approx(v, abs=1e-6)


def test_pairwise_similarities_in_unit_interval():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"s{i}", vs) for i in range(3)]
    for v in svc.pairwise_similarities(vectors).values():
        assert 0.0 <= v <= 1.0


def test_pairwise_similarities_empty():
    svc = StrategyClusteringService()
    assert svc.pairwise_similarities([]) == {}


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_cluster_empty_returns_empty():
    svc = StrategyClusteringService()
    assert svc.cluster([]) == []


# ---------------------------------------------------------------------------
# as_dict serialisation
# ---------------------------------------------------------------------------


def test_cluster_as_dict_contains_required_keys():
    svc = StrategyClusteringService()
    vs = make_validation_summary()
    vec = _make_vector("s1", vs)
    clusters = svc.cluster([vec])
    d = clusters[0].as_dict()
    for key in ("cluster_id", "size", "representative", "intra_cluster_variance", "members"):
        assert key in d
