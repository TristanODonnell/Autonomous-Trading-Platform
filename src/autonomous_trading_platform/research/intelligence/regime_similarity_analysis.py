"""
Regime similarity analysis (TASK-2.5).

Builds RegimeFingerprint objects from StrategyRegimeProfile and supports:
  - regime-aware clustering
  - regime diversification analysis
  - regime specialisation detection
  - comparison across strategies

All analysis is deterministic and offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_EPSILON = 1e-9
_NEUTRAL_SHARPE_NORM = 0.5  # 0.5 maps to Sharpe=0


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _normalise_sharpe(sharpe: float) -> float:
    """Map Sharpe to [0,1]: 0 → 0.5; ±3 → ~1.0/~0.0."""
    return _clamp(0.5 + sharpe / 6.0)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < _EPSILON or nb < _EPSILON:
        return 0.0
    return dot / (na * nb)


# Stable dimension ordering for fingerprint vector construction
_DIMENSIONS = ("trend", "volatility", "liquidity", "mean_reversion", "risk")
_LABELS_PER_DIM = {
    "trend": ("bull", "bear", "sideways"),
    "volatility": ("high_volatility", "normal_volatility", "low_volatility"),
    "liquidity": ("high_liquidity", "normal_liquidity", "low_liquidity"),
    "mean_reversion": ("trending", "mean_reverting", "undefined"),
    "risk": ("risk_on", "risk_off", "neutral"),
}


@dataclass(frozen=True)
class RegimeFingerprint:
    """
    Regime-conditioned performance profile for a single strategy.

    fingerprint_vector encodes per-label Sharpe (normalised) plus
    sensitivity scores across all 5 dimensions in a fixed order.
    """

    strategy_id: str
    experiment_id: str

    # Per-label Sharpe by dimension (normalised to [0,1])
    sharpe_by_dim_label: dict[str, dict[str, float | None]]

    # Per-dimension sensitivity scores (0=robust, 1=highly sensitive)
    sensitivity_by_dimension: dict[str, float]

    # Overall characteristics
    dominant_regime_dimension: str | None  # dimension with highest average Sharpe
    regime_diversity: float  # [0,1]: 1 = works equally in all regimes
    is_regime_specialist: bool  # True if strongly dominant in one dimension
    overall_robustness: float  # [0,1]: fraction of regime labels with positive Sharpe

    # Stable numeric vector for clustering / distance computations
    # Length = 5*3 (per-label Sharpes) + 5 (sensitivities) = 20 elements
    fingerprint_vector: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "experiment_id": self.experiment_id,
            "regime_diversity": round(self.regime_diversity, 4),
            "overall_robustness": round(self.overall_robustness, 4),
            "dominant_regime_dimension": self.dominant_regime_dimension,
            "is_regime_specialist": self.is_regime_specialist,
            "sensitivity_by_dimension": {
                k: round(v, 4) for k, v in self.sensitivity_by_dimension.items()
            },
        }


class RegimeSimilarityAnalyzer:
    """
    Builds regime fingerprints from StrategyRegimeProfile and supports
    pairwise comparison and regime-specialisation detection.

    Parameters
    ----------
    specialist_dominance_threshold
        A strategy is a regime-specialist if one dimension's average
        normalised Sharpe exceeds this threshold while others are low.
    """

    def __init__(self, *, specialist_dominance_threshold: float = 0.70) -> None:
        self._specialist_threshold = specialist_dominance_threshold

    def build_fingerprint(
        self,
        strategy_id: str,
        experiment_id: str,
        regime_profile: Any,  # StrategyRegimeProfile
    ) -> RegimeFingerprint:
        dim_summaries = {
            "trend": regime_profile.by_trend,
            "volatility": regime_profile.by_volatility,
            "liquidity": regime_profile.by_liquidity,
            "mean_reversion": regime_profile.by_mean_reversion,
            "risk": regime_profile.by_risk,
        }

        # Build per-label Sharpe map (normalised)
        sharpe_by_dim_label: dict[str, dict[str, float | None]] = {}
        for dim in _DIMENSIONS:
            dim_summary = dim_summaries.get(dim)
            sharpe_by_dim_label[dim] = {}
            if dim_summary is None:
                for label in _LABELS_PER_DIM[dim]:
                    sharpe_by_dim_label[dim][label] = None
                continue
            for label in _LABELS_PER_DIM.get(dim, ()):
                m = dim_summary.metrics_by_label.get(label)
                if m is not None and m.sharpe is not None:
                    sharpe_by_dim_label[dim][label] = _normalise_sharpe(m.sharpe)
                else:
                    sharpe_by_dim_label[dim][label] = None

        # Sensitivity by dimension (normalised: 0=fully sensitive, 1=robust)
        sensitivity_by_dimension: dict[str, float] = {}
        for dim in _DIMENSIONS:
            dim_summary = dim_summaries.get(dim)
            if dim_summary is None:
                sensitivity_by_dimension[dim] = 0.0
            else:
                s = dim_summary.sensitivity
                # sharpe_std: 0 = no sensitivity, >1 = high sensitivity
                sensitivity_by_dimension[dim] = _clamp(s.sharpe_std / 2.0)

        # Overall robustness: fraction of regime labels with Sharpe > 0
        positive_count = 0
        total_with_data = 0
        for _dim, labels in sharpe_by_dim_label.items():
            for norm_sharpe in labels.values():
                if norm_sharpe is not None:
                    total_with_data += 1
                    if norm_sharpe > _NEUTRAL_SHARPE_NORM:
                        positive_count += 1
        overall_robustness = positive_count / total_with_data if total_with_data > 0 else 0.0

        # Regime diversity: 1 - std of dimension averages (higher = more uniform)
        dim_avg_sharpes: dict[str, float] = {}
        for dim in _DIMENSIONS:
            vals = [v for v in sharpe_by_dim_label[dim].values() if v is not None]
            dim_avg_sharpes[dim] = sum(vals) / len(vals) if vals else _NEUTRAL_SHARPE_NORM

        all_avgs = list(dim_avg_sharpes.values())
        mean_avg = sum(all_avgs) / len(all_avgs) if all_avgs else _NEUTRAL_SHARPE_NORM
        variance = sum((x - mean_avg) ** 2 for x in all_avgs) / len(all_avgs) if all_avgs else 0.0
        std = math.sqrt(variance)
        regime_diversity = _clamp(1.0 - std / 0.5)  # std=0 → diversity=1; std=0.5 → diversity=0

        # Dominant dimension
        dominant = (
            max(dim_avg_sharpes, key=lambda k: dim_avg_sharpes[k]) if dim_avg_sharpes else None
        )

        # Regime specialist: dominant dimension significantly above others
        is_specialist = False
        if dominant is not None and dim_avg_sharpes[dominant] >= self._specialist_threshold:
            others = [v for k, v in dim_avg_sharpes.items() if k != dominant]
            if others and (dim_avg_sharpes[dominant] - max(others)) >= 0.20:
                is_specialist = True

        # Build fingerprint vector (stable 20-element vector)
        fp_vector: list[float] = []
        for dim in _DIMENSIONS:
            for label in _LABELS_PER_DIM[dim]:
                v = sharpe_by_dim_label[dim].get(label)
                fp_vector.append(v if v is not None else _NEUTRAL_SHARPE_NORM)
        for dim in _DIMENSIONS:
            fp_vector.append(sensitivity_by_dimension[dim])

        return RegimeFingerprint(
            strategy_id=strategy_id,
            experiment_id=experiment_id,
            sharpe_by_dim_label=sharpe_by_dim_label,
            sensitivity_by_dimension=sensitivity_by_dimension,
            dominant_regime_dimension=dominant,
            regime_diversity=regime_diversity,
            is_regime_specialist=is_specialist,
            overall_robustness=overall_robustness,
            fingerprint_vector=tuple(fp_vector),
        )

    def similarity(
        self,
        fp1: RegimeFingerprint,
        fp2: RegimeFingerprint,
    ) -> float:
        """Cosine similarity of two fingerprint vectors in [0, 1]."""
        raw = _cosine(fp1.fingerprint_vector, fp2.fingerprint_vector)
        return _clamp((raw + 1.0) / 2.0)

    def pairwise_similarities(
        self,
        fingerprints: list[RegimeFingerprint],
    ) -> dict[tuple[str, str], float]:
        result: dict[tuple[str, str], float] = {}
        for i, fp1 in enumerate(fingerprints):
            for j, fp2 in enumerate(fingerprints):
                if i >= j:
                    continue
                sim = self.similarity(fp1, fp2)
                result[(fp1.strategy_id, fp2.strategy_id)] = sim
                result[(fp2.strategy_id, fp1.strategy_id)] = sim
        return result

    def detect_regime_specialists(
        self,
        fingerprints: list[RegimeFingerprint],
    ) -> dict[str, list[str]]:
        """Group strategy IDs by their dominant regime dimension (specialists only)."""
        groups: dict[str, list[str]] = {}
        for fp in fingerprints:
            if fp.is_regime_specialist and fp.dominant_regime_dimension is not None:
                key = fp.dominant_regime_dimension
                groups.setdefault(key, []).append(fp.strategy_id)
        return groups

    def regime_diversification_score(
        self,
        fingerprints: list[RegimeFingerprint],
    ) -> float:
        """
        Score [0,1] measuring how well a portfolio of strategies covers regimes.

        1.0 = strategies are maximally diverse in regime exposure.
        0.0 = all strategies are identical in regime profile.
        """
        if not fingerprints:
            return 0.0
        if len(fingerprints) == 1:
            return fingerprints[0].regime_diversity

        similarities = self.pairwise_similarities(fingerprints)
        if not similarities:
            return 1.0
        avg_sim = sum(similarities.values()) / len(similarities)
        return _clamp(1.0 - avg_sim)
