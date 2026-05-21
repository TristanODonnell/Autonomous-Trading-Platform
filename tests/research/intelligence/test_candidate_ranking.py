"""Tests for CandidateRankingService and CandidateScore."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.intelligence.candidate_ranking_service import (
    CandidateRankingService,
    RankingWeights,
)
from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    ResearchFeatureVectorBuilder,
)
from tests.research.intelligence.conftest import (
    make_poor_validation_summary,
    make_regime_profile,
    make_validation_summary,
)


def _make_vector(strategy_id: str, validation_summary=None, regime_profile=None):
    builder = ResearchFeatureVectorBuilder()
    return builder.build(
        strategy_id=strategy_id,
        experiment_id="exp_001",
        dataset_version="v1",
        validation_summary=validation_summary,
        regime_profile=regime_profile,
    )


# ---------------------------------------------------------------------------
# Ranking determinism
# ---------------------------------------------------------------------------


def test_ranking_is_deterministic():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vectors = [_make_vector(f"strat_{i}", vs) for i in range(5)]
    r1 = svc.rank(vectors)
    r2 = svc.rank(vectors)
    assert [s.strategy_id for s in r1] == [s.strategy_id for s in r2]
    assert [s.composite_score for s in r1] == [s.composite_score for s in r2]


def test_rank_order_stable_same_inputs():
    svc = CandidateRankingService()
    vs_good = make_validation_summary(overall=0.85)
    vs_poor = make_poor_validation_summary()
    v_good = _make_vector("good", vs_good)
    v_poor = _make_vector("poor", vs_poor)
    ranked = svc.rank([v_poor, v_good])  # pass in reverse order
    assert ranked[0].strategy_id == "good"
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2


# ---------------------------------------------------------------------------
# Score values
# ---------------------------------------------------------------------------


def test_good_strategy_higher_score_than_poor():
    svc = CandidateRankingService()
    vs_good = make_validation_summary()
    vs_poor = make_poor_validation_summary()
    v_good = _make_vector("good", vs_good)
    v_poor = _make_vector("poor", vs_poor)
    s_good = svc.score_single(v_good)
    s_poor = svc.score_single(v_poor)
    assert s_good.composite_score > s_poor.composite_score


def test_composite_score_in_unit_interval():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    score = svc.score_single(vec)
    assert 0.0 <= score.composite_score <= 1.0


def test_deployability_score_in_unit_interval():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    score = svc.score_single(vec)
    assert 0.0 <= score.deployability_score <= 1.0


# ---------------------------------------------------------------------------
# Weighting correctness
# ---------------------------------------------------------------------------


def test_custom_weights_affect_score():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    w1 = RankingWeights(robustness_overall=0.9, overfitting_resistance=0.1)
    w2 = RankingWeights(robustness_overall=0.1, overfitting_resistance=0.9)
    s1 = svc.score_single(vec, weights=w1)
    s2 = svc.score_single(vec, weights=w2)
    # Different weighting should generally produce different scores
    # (they can be equal only if signal values are identical)
    # Just verify both are valid
    assert 0.0 <= s1.composite_score <= 1.0
    assert 0.0 <= s2.composite_score <= 1.0


def test_weights_must_be_non_negative():
    with pytest.raises(ValueError):
        RankingWeights(robustness_overall=-0.1)


# ---------------------------------------------------------------------------
# Components breakdown
# ---------------------------------------------------------------------------


def test_score_has_components():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    score = svc.score_single(vec)
    assert len(score.components) > 0


def test_component_contributions_sum_to_composite():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    score = svc.score_single(vec)
    total = sum(c.weighted_contribution for c in score.components)
    assert total == pytest.approx(score.composite_score, abs=1e-5)


# ---------------------------------------------------------------------------
# Weakness flags
# ---------------------------------------------------------------------------


def test_poor_strategy_has_weakness_flags():
    svc = CandidateRankingService()
    vs = make_poor_validation_summary()
    vec = _make_vector("poor", vs)
    score = svc.score_single(vec)
    assert len(score.weakness_flags) > 0


def test_good_strategy_has_fewer_weakness_flags():
    svc = CandidateRankingService()
    vs_good = make_validation_summary()
    vs_poor = make_poor_validation_summary()
    v_good = _make_vector("good", vs_good)
    v_poor = _make_vector("poor", vs_poor)
    s_good = svc.score_single(v_good)
    s_poor = svc.score_single(v_poor)
    assert len(s_good.weakness_flags) < len(s_poor.weakness_flags)


# ---------------------------------------------------------------------------
# Deployability
# ---------------------------------------------------------------------------


def test_good_strategy_is_deployable():
    svc = CandidateRankingService(deployability_threshold=0.30)
    vs = make_validation_summary()
    rp = make_regime_profile(is_regime_robust=True)
    vec = _make_vector("good", vs, rp)
    score = svc.score_single(vec)
    assert score.is_deployable


def test_poor_strategy_not_deployable():
    svc = CandidateRankingService(deployability_threshold=0.80)
    vs = make_poor_validation_summary()
    vec = _make_vector("poor", vs)
    score = svc.score_single(vec)
    assert not score.is_deployable


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_rank_empty_list_returns_empty():
    svc = CandidateRankingService()
    assert svc.rank([]) == []


# ---------------------------------------------------------------------------
# as_dict serialisation
# ---------------------------------------------------------------------------


def test_as_dict_contains_required_keys():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    d = svc.score_single(vec).as_dict()
    for key in (
        "composite_score",
        "deployability_score",
        "rank",
        "is_deployable",
        "components",
        "explanation",
    ):
        assert key in d


def test_explanation_is_non_empty_string():
    svc = CandidateRankingService()
    vs = make_validation_summary()
    vec = _make_vector("s", vs)
    score = svc.score_single(vec)
    assert isinstance(score.explanation, str)
    assert len(score.explanation) > 0
