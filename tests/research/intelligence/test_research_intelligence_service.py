"""Tests for ResearchIntelligenceService — integration of all intelligence components."""

from __future__ import annotations

from autonomous_trading_platform.research.intelligence.research_intelligence_service import (
    ResearchIntelligenceRequest,
    ResearchIntelligenceService,
)
from autonomous_trading_platform.research.intelligence.research_intelligence_summary import (
    ResearchIntelligenceSummary,
)
from tests.research.intelligence.conftest import (
    make_poor_validation_summary,
    make_regime_profile,
    make_validation_summary,
)


def _make_request(
    strategy_id: str = "strat_001",
    *,
    validation_summary=None,
    regime_profile=None,
    family: str | None = None,
    persist: bool = False,
) -> ResearchIntelligenceRequest:
    return ResearchIntelligenceRequest(
        strategy_id=strategy_id,
        experiment_id="exp_test",
        dataset_version="v1",
        run_id="run-test-001",
        validation_summary=validation_summary,
        regime_profile=regime_profile,
        strategy_family=family,
        persist=persist,
    )


# ---------------------------------------------------------------------------
# Single analysis
# ---------------------------------------------------------------------------


def test_analyze_returns_summary():
    svc = ResearchIntelligenceService()
    req = _make_request()
    result = svc.analyze(req)
    assert isinstance(result, ResearchIntelligenceSummary)


def test_analyze_with_all_inputs():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    rp = make_regime_profile()
    req = _make_request(validation_summary=vs, regime_profile=rp, family="momentum")
    result = svc.analyze(req)
    assert result.feature_vector is not None
    assert result.candidate_score is not None
    assert result.robustness_estimate is not None
    assert result.overfit_estimate is not None
    assert result.regime_fingerprint is not None


def test_analyze_without_regime_profile_has_no_fingerprint():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    req = _make_request(validation_summary=vs)
    result = svc.analyze(req)
    assert result.regime_fingerprint is None


def test_strategy_id_preserved():
    svc = ResearchIntelligenceService()
    req = _make_request("my_unique_strategy_id")
    result = svc.analyze(req)
    assert result.strategy_id == "my_unique_strategy_id"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_same_inputs():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    req = _make_request(validation_summary=vs)
    r1 = svc.analyze(req)
    r2 = svc.analyze(req)
    assert r1.feature_vector.config_hash == r2.feature_vector.config_hash
    assert r1.candidate_score.composite_score == r2.candidate_score.composite_score
    assert (
        r1.robustness_estimate.robustness_probability
        == r2.robustness_estimate.robustness_probability
    )


# ---------------------------------------------------------------------------
# Batch ranking
# ---------------------------------------------------------------------------


def test_rank_candidates_returns_sorted_list():
    svc = ResearchIntelligenceService()
    vs_good = make_validation_summary()
    vs_poor = make_poor_validation_summary()
    r_good = svc.analyze(_make_request("good", validation_summary=vs_good))
    r_poor = svc.analyze(_make_request("poor", validation_summary=vs_poor))
    ranked = svc.rank_candidates([r_poor, r_good])
    assert ranked[0][0] == 1
    assert ranked[0][1].strategy_id == "good"
    assert ranked[1][0] == 2


def test_rank_candidates_empty():
    svc = ResearchIntelligenceService()
    assert svc.rank_candidates([]) == []


def test_rank_candidates_updates_rank():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    summaries = [svc.analyze(_make_request(f"s{i}", validation_summary=vs)) for i in range(3)]
    ranked = svc.rank_candidates(summaries)
    ranks = [r for r, _ in ranked]
    assert sorted(ranks) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Batch clustering
# ---------------------------------------------------------------------------


def test_cluster_candidates_returns_clusters():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    summaries = [svc.analyze(_make_request(f"s{i}", validation_summary=vs)) for i in range(4)]
    clusters = svc.cluster_candidates(summaries)
    assert len(clusters) >= 1


def test_cluster_candidates_assigns_cluster_ids():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    summaries = [svc.analyze(_make_request(f"s{i}", validation_summary=vs)) for i in range(3)]
    svc.cluster_candidates(summaries)
    for s in summaries:
        assert s.cluster_id is not None


def test_cluster_empty_list():
    svc = ResearchIntelligenceService()
    assert svc.cluster_candidates([]) == []


# ---------------------------------------------------------------------------
# Regime diversity analysis
# ---------------------------------------------------------------------------


def test_regime_diversity_analysis_with_fingerprints():
    svc = ResearchIntelligenceService()
    rp = make_regime_profile()
    vs = make_validation_summary()
    summaries = [
        svc.analyze(_make_request(f"s{i}", validation_summary=vs, regime_profile=rp))
        for i in range(3)
    ]
    result = svc.analyze_regime_diversity(summaries)
    assert "regime_specialists" in result
    assert "diversification_score" in result
    assert 0.0 <= result["diversification_score"] <= 1.0


def test_regime_diversity_empty_fingerprints():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    summaries = [svc.analyze(_make_request(f"s{i}", validation_summary=vs)) for i in range(3)]
    result = svc.analyze_regime_diversity(summaries)
    assert result["diversification_score"] == 0.0


# ---------------------------------------------------------------------------
# as_dict
# ---------------------------------------------------------------------------


def test_summary_as_dict_has_required_keys():
    svc = ResearchIntelligenceService()
    vs = make_validation_summary()
    result = svc.analyze(_make_request(validation_summary=vs))
    d = result.as_dict()
    for key in (
        "strategy_id",
        "experiment_id",
        "computed_at",
        "candidate_score",
        "robustness_estimate",
        "overfit_estimate",
        "feature_vector",
    ):
        assert key in d
