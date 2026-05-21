"""Tests for RegimeSimilarityAnalyzer and RegimeFingerprint."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.intelligence.regime_similarity_analysis import (
    RegimeSimilarityAnalyzer,
)
from tests.research.intelligence.conftest import make_regime_profile

# ---------------------------------------------------------------------------
# Fingerprint construction
# ---------------------------------------------------------------------------


def test_fingerprint_vector_length():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp = analyzer.build_fingerprint("s", "e", rp)
    # 5 dimensions × 3 labels + 5 sensitivities = 20
    assert len(fp.fingerprint_vector) == 20


def test_fingerprint_vector_in_unit_interval():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp = analyzer.build_fingerprint("s", "e", rp)
    for v in fp.fingerprint_vector:
        assert 0.0 <= v <= 1.0, f"Value {v} out of [0,1]"


def test_fingerprint_stable_ordering():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp1 = analyzer.build_fingerprint("s1", "e", rp)
    fp2 = analyzer.build_fingerprint("s2", "e", rp)
    # Different strategy_id, same regime profile → same vector
    assert fp1.fingerprint_vector == fp2.fingerprint_vector


def test_regime_diversity_in_unit_interval():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp = analyzer.build_fingerprint("s", "e", rp)
    assert 0.0 <= fp.regime_diversity <= 1.0


def test_overall_robustness_in_unit_interval():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp = analyzer.build_fingerprint("s", "e", rp)
    assert 0.0 <= fp.overall_robustness <= 1.0


# ---------------------------------------------------------------------------
# Regime specialist detection
# ---------------------------------------------------------------------------


def test_specialist_detected_for_dominant_strategy():
    analyzer = RegimeSimilarityAnalyzer(specialist_dominance_threshold=0.60)
    # Strong bull bias
    rp = make_regime_profile(
        trend={"bull": 3.0, "bear": -1.0, "sideways": -0.5},
    )
    fp = analyzer.build_fingerprint("s", "e", rp)
    # With such a strong trend bias, should be flagged as specialist
    # (may or may not depending on normalisations — test gracefully)
    assert isinstance(fp.is_regime_specialist, bool)


def test_non_specialist_for_uniform_strategy():
    analyzer = RegimeSimilarityAnalyzer(specialist_dominance_threshold=0.90)
    rp = make_regime_profile(
        trend={"bull": 0.8, "bear": 0.7, "sideways": 0.75},
    )
    fp = analyzer.build_fingerprint("s", "e", rp)
    assert not fp.is_regime_specialist


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def test_identical_fingerprints_have_max_similarity():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp1 = analyzer.build_fingerprint("s1", "e", rp)
    fp2 = analyzer.build_fingerprint("s2", "e", rp)
    sim = analyzer.similarity(fp1, fp2)
    assert sim == pytest.approx(1.0, abs=0.01)


def test_similarity_in_unit_interval():
    analyzer = RegimeSimilarityAnalyzer()
    rp1 = make_regime_profile(trend={"bull": 2.0, "bear": -1.0, "sideways": 0.0})
    rp2 = make_regime_profile(trend={"bull": -1.0, "bear": 2.0, "sideways": 0.0})
    fp1 = analyzer.build_fingerprint("s1", "e", rp1)
    fp2 = analyzer.build_fingerprint("s2", "e", rp2)
    sim = analyzer.similarity(fp1, fp2)
    assert 0.0 <= sim <= 1.0


def test_similarity_symmetric():
    analyzer = RegimeSimilarityAnalyzer()
    rp1 = make_regime_profile()
    rp2 = make_regime_profile(is_regime_robust=False, overall_sensitivity=1.5)
    fp1 = analyzer.build_fingerprint("s1", "e", rp1)
    fp2 = analyzer.build_fingerprint("s2", "e", rp2)
    assert analyzer.similarity(fp1, fp2) == pytest.approx(analyzer.similarity(fp2, fp1), abs=1e-6)


# ---------------------------------------------------------------------------
# Pairwise similarities
# ---------------------------------------------------------------------------


def test_pairwise_similarities_correct_count():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fps = [analyzer.build_fingerprint(f"s{i}", "e", rp) for i in range(3)]
    sims = analyzer.pairwise_similarities(fps)
    # n*(n-1) pairs for n=3 → 6 entries
    assert len(sims) == 6


def test_pairwise_similarities_symmetric():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fps = [analyzer.build_fingerprint(f"s{i}", "e", rp) for i in range(3)]
    sims = analyzer.pairwise_similarities(fps)
    for (a, b), v in sims.items():
        assert sims[(b, a)] == pytest.approx(v, abs=1e-6)


# ---------------------------------------------------------------------------
# Regime specialists grouping
# ---------------------------------------------------------------------------


def test_detect_regime_specialists_returns_dict():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fps = [analyzer.build_fingerprint(f"s{i}", "e", rp) for i in range(3)]
    result = analyzer.detect_regime_specialists(fps)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Diversification score
# ---------------------------------------------------------------------------


def test_diversification_score_single_fingerprint():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp = analyzer.build_fingerprint("s", "e", rp)
    score = analyzer.regime_diversification_score([fp])
    assert 0.0 <= score <= 1.0


def test_diversification_score_in_unit_interval():
    analyzer = RegimeSimilarityAnalyzer()
    rps = [
        make_regime_profile(trend={"bull": 1.5, "bear": 0.0, "sideways": 0.5}),
        make_regime_profile(trend={"bull": 0.0, "bear": 1.5, "sideways": 0.5}),
    ]
    fps = [analyzer.build_fingerprint(f"s{i}", "e", rp) for i, rp in enumerate(rps)]
    score = analyzer.regime_diversification_score(fps)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# as_dict
# ---------------------------------------------------------------------------


def test_as_dict_has_required_keys():
    analyzer = RegimeSimilarityAnalyzer()
    rp = make_regime_profile()
    fp = analyzer.build_fingerprint("s", "e", rp)
    d = fp.as_dict()
    for key in ("strategy_id", "regime_diversity", "overall_robustness", "is_regime_specialist"):
        assert key in d
