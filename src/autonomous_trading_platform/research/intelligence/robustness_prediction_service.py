"""
Robustness prediction service (TASK-2.5).

Estimates deployment robustness from existing validation artifacts.
All estimation is statistical/heuristic — no black-box ML models.

Produces:
  - robustness_probability [0, 1]
  - fragility_score [0, 1]
  - deployment_suitability (SUITABLE / BORDERLINE / UNSUITABLE)
  - confidence (fraction of validation signals available)
  - explanation list
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    _NEUTRAL,
    ResearchFeatureVector,
    _clamp,
)


class DeploymentSuitability(StrEnum):
    SUITABLE = "suitable"
    BORDERLINE = "borderline"
    UNSUITABLE = "unsuitable"


@dataclass(frozen=True)
class RobustnessEstimate:
    """
    Estimated deployment robustness for a strategy candidate.

    robustness_probability is the estimated probability that the strategy
    will generalise beyond the in-sample period.

    fragility_score is its complement with contextual adjustments.
    """

    strategy_id: str
    robustness_probability: float  # [0, 1]; higher = more robust
    fragility_score: float  # [0, 1]; higher = more fragile
    confidence: float  # [0, 1]; based on data completeness
    deployment_suitability: DeploymentSuitability
    primary_weakness: str | None
    explanation: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "robustness_probability": round(self.robustness_probability, 4),
            "fragility_score": round(self.fragility_score, 4),
            "confidence": round(self.confidence, 4),
            "deployment_suitability": self.deployment_suitability.value,
            "primary_weakness": self.primary_weakness,
            "explanation": self.explanation,
        }


class RobustnessPredictionService:
    """
    Estimates deployment robustness from existing validation feature vectors.

    Aggregation approach
    --------------------
    A weighted average of primary robustness signals — walk-forward, stress,
    regime, overfit — with a confidence-scaled adjustment for data sparsity.

    Fragility is detected via any single catastrophic signal falling below
    a hard floor, regardless of the average.
    """

    # Signal field → (weight, label)
    _SIGNAL_WEIGHTS: dict[str, tuple[float, str]] = {
        "val_robustness_overall": (0.30, "overall validation robustness"),
        "val_walk_forward_consistency": (0.20, "walk-forward time consistency"),
        "overfit_resistance": (0.20, "overfitting resistance"),
        "val_regime_robustness": (0.15, "regime robustness"),
        "val_stress_resilience": (0.10, "stress scenario survival"),
        "wf_fold_sharpe_stability": (0.05, "fold-to-fold Sharpe stability"),
    }

    # Hard fragility floors: if any of these fall below the threshold, flag fragility
    _FRAGILITY_FLOORS: list[tuple[str, float, str]] = [
        ("val_walk_forward_consistency", 0.25, "very low walk-forward consistency"),
        ("overfit_resistance", 0.20, "very high overfitting risk"),
        ("val_regime_robustness", 0.15, "catastrophic regime failure"),
        ("val_stress_resilience", 0.15, "fails nearly all stress scenarios"),
        ("overfit_train_test_degradation", 0.20, "extreme train/test degradation"),
    ]

    def __init__(
        self,
        *,
        suitable_threshold: float = 0.60,
        borderline_threshold: float = 0.40,
    ) -> None:
        self._suitable = suitable_threshold
        self._borderline = borderline_threshold

    def estimate(
        self,
        vector: ResearchFeatureVector,
    ) -> RobustnessEstimate:
        fv = vector.as_dict()
        explanation: list[str] = []

        # Weighted aggregate over available signals
        total_weight = 0.0
        weighted_sum = 0.0
        for field_name, (weight, label) in self._SIGNAL_WEIGHTS.items():
            v = fv.get(field_name)
            if v is None or v == _NEUTRAL and not self._field_has_data(vector, field_name):
                explanation.append(f"  {label}: no data (skipped)")
                continue
            total_weight += weight
            weighted_sum += weight * v
            explanation.append(f"  {label}: {v:.3f} × {weight:.2f} = {weight * v:.4f}")

        robustness_prob = _clamp(weighted_sum / total_weight) if total_weight > 0 else _NEUTRAL

        # Confidence = data completeness weighted by signal importance
        confidence = min(1.0, total_weight / sum(w for w, _ in self._SIGNAL_WEIGHTS.values()))

        # Fragility check: any catastrophic single signal
        fragility_flags: list[str] = []
        for field_name, floor, reason in self._FRAGILITY_FLOORS:
            v = fv.get(field_name, _NEUTRAL)
            if self._field_has_data(vector, field_name) and v < floor:
                fragility_flags.append(reason)

        # Compute fragility
        fragility = _clamp(1.0 - robustness_prob)
        if fragility_flags:
            # Boost fragility for catastrophic signals
            fragility = _clamp(fragility + 0.15 * len(fragility_flags))
            robustness_prob = _clamp(1.0 - fragility)
            for flag in fragility_flags:
                explanation.append(f"  FRAGILITY: {flag}")

        # Suitability
        if robustness_prob >= self._suitable and not fragility_flags:
            suitability = DeploymentSuitability.SUITABLE
        elif robustness_prob >= self._borderline:
            suitability = DeploymentSuitability.BORDERLINE
        else:
            suitability = DeploymentSuitability.UNSUITABLE

        # Primary weakness: lowest-scoring field with data
        primary = self._primary_weakness(fv, vector)

        explanation.insert(
            0,
            f"Robustness probability: {robustness_prob:.3f} "
            f"(confidence={confidence:.2f}, suitability={suitability.value})",
        )

        return RobustnessEstimate(
            strategy_id=vector.strategy_id,
            robustness_probability=robustness_prob,
            fragility_score=fragility,
            confidence=confidence,
            deployment_suitability=suitability,
            primary_weakness=primary,
            explanation=explanation,
        )

    def _field_has_data(self, vector: ResearchFeatureVector, field_name: str) -> bool:
        for f in vector.fields:
            if f.name == field_name:
                return f.has_data
        return False

    def _primary_weakness(
        self,
        fv: dict[str, float],
        vector: ResearchFeatureVector,
    ) -> str | None:
        candidates: list[tuple[float, str]] = []
        for field_name, (_, label) in self._SIGNAL_WEIGHTS.items():
            if not self._field_has_data(vector, field_name):
                continue
            v = fv.get(field_name, _NEUTRAL)
            if v < 0.5:
                candidates.append((v, label))
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[0])[1]
