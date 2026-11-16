"""
Deterministic candidate ranking service (TASK-2.5).

Ranks strategy candidates by composite weighted score derived from
existing validation and regime signals.  All ranking is:

  - deterministic (same inputs → same order)
  - decomposable (per-component breakdown)
  - explainable (human-readable explanation per candidate)
  - configurable (weights can be overridden)

This service does NOT promote strategies or enable trading.
It is a prioritisation tool for research review.

Which signals count is set by an EvidencePolicy
(research/validation/evidence_policy.py). By default only walk-forward and
Monte Carlo decide: the composite uses the overall robustness score (built
from those two) and walk-forward consistency, and deployability is the plain
average of walk-forward consistency and Monte Carlo stability. Every other
component is listed with weight 0. A candidate missing a required signal is
not scored: composite and deployability are 0.0, evidence_complete is False,
it ranks last and is never deployable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    _NEUTRAL,
    ResearchFeatureVector,
    _clamp,
)
from autonomous_trading_platform.research.validation.evidence_policy import (
    COMPONENTS,
    DEFAULT_EVIDENCE_POLICY,
    OVERFITTING,
    EvidencePolicy,
)

# Ranking signals that are not robustness components, and the component each
# follows: the overall score is built from deciding components only, so it
# always counts; the trade-count flag is an output of the overfitting analysis.
_ALWAYS_DECIDES = "robustness_overall"
_FOLLOWS_COMPONENT = {"trade_reliability": OVERFITTING}


@dataclass(frozen=True)
class RankingWeights:
    """
    Configurable weights for the composite candidate score.

    Weights are normalised at runtime so they do not need to sum to 1.
    """

    robustness_overall: float = 0.25
    overfitting_resistance: float = 0.20
    regime_robustness: float = 0.15
    walk_forward_consistency: float = 0.15
    stress_resilience: float = 0.10
    parameter_stability: float = 0.10
    trade_reliability: float = 0.05

    def __post_init__(self) -> None:
        for attr in self.__dataclass_fields__:
            v = getattr(self, attr)
            if v < 0.0:
                raise ValueError(f"RankingWeights.{attr} must be >= 0, got {v}")


@dataclass(frozen=True)
class RankingComponent:
    """Contribution of a single signal to the composite score."""

    name: str
    raw_value: float | None
    normalised_value: float
    weight: float
    weighted_contribution: float
    explanation: str


@dataclass(frozen=True)
class CandidateScore:
    """
    Composite ranking score for a single strategy candidate.

    composite_score is in [0, 1].  Higher is better.
    rank is 1-based (rank=1 is the best candidate in the batch).
    """

    strategy_id: str
    experiment_id: str
    rank: int
    composite_score: float
    components: tuple[RankingComponent, ...]
    weakness_flags: tuple[str, ...]
    deployability_score: float  # simplified deployment-readiness metric
    is_deployable: bool  # deployability_score >= threshold
    explanation: str
    data_completeness: float
    evidence_complete: bool = True  # False: a required signal was missing, not scored
    missing_evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "experiment_id": self.experiment_id,
            "rank": self.rank,
            "composite_score": round(self.composite_score, 4),
            "deployability_score": round(self.deployability_score, 4),
            "is_deployable": self.is_deployable,
            "evidence_complete": self.evidence_complete,
            "missing_evidence": list(self.missing_evidence),
            "data_completeness": round(self.data_completeness, 4),
            "explanation": self.explanation,
            "weakness_flags": list(self.weakness_flags),
            "components": [
                {
                    "name": c.name,
                    "value": round(c.normalised_value, 4),
                    "weight": round(c.weight, 4),
                    "contribution": round(c.weighted_contribution, 4),
                    "explanation": c.explanation,
                }
                for c in self.components
            ],
        }


class CandidateRankingService:
    """
    Ranks a collection of strategy feature vectors by composite weighted score.

    Parameters
    ----------
    deployability_threshold
        Minimum deployability_score for a candidate to be flagged as deployable.
    evidence_policy
        Which robustness components decide and which must be present.
    """

    def __init__(
        self,
        *,
        deployability_threshold: float = 0.55,
        evidence_policy: EvidencePolicy | None = None,
    ) -> None:
        self._deploy_threshold = deployability_threshold
        self._policy = evidence_policy or DEFAULT_EVIDENCE_POLICY

    def rank(
        self,
        vectors: list[ResearchFeatureVector],
        weights: RankingWeights | None = None,
    ) -> list[CandidateScore]:
        """Rank all candidates and return sorted list (best first)."""
        if not vectors:
            return []
        w = weights or RankingWeights()
        raw_scores = [self._score_vector(v, w) for v in vectors]
        raw_scores.sort(key=lambda s: -s[0])  # descending by composite_score

        results: list[CandidateScore] = []
        for rank, (composite, components, weaknesses, deploy, vec, missing) in enumerate(
            raw_scores, start=1
        ):
            results.append(
                CandidateScore(
                    strategy_id=vec.strategy_id,
                    experiment_id=vec.experiment_id,
                    rank=rank,
                    composite_score=composite,
                    components=components,
                    weakness_flags=weaknesses,
                    deployability_score=deploy,
                    is_deployable=not missing and deploy >= self._deploy_threshold,
                    explanation=self._explain(composite, components, weaknesses, deploy, missing),
                    data_completeness=vec.data_completeness,
                    evidence_complete=not missing,
                    missing_evidence=missing,
                )
            )
        return results

    def score_single(
        self,
        vector: ResearchFeatureVector,
        weights: RankingWeights | None = None,
        rank: int = 1,
    ) -> CandidateScore:
        """Score a single candidate (rank defaults to 1)."""
        w = weights or RankingWeights()
        composite, components, weaknesses, deploy, _, missing = self._score_vector(vector, w)
        return CandidateScore(
            strategy_id=vector.strategy_id,
            experiment_id=vector.experiment_id,
            rank=rank,
            composite_score=composite,
            components=components,
            weakness_flags=weaknesses,
            deployability_score=deploy,
            is_deployable=not missing and deploy >= self._deploy_threshold,
            explanation=self._explain(composite, components, weaknesses, deploy, missing),
            data_completeness=vector.data_completeness,
            evidence_complete=not missing,
            missing_evidence=missing,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_vector(
        self,
        vec: ResearchFeatureVector,
        w: RankingWeights,
    ) -> tuple[
        float,
        tuple[RankingComponent, ...],
        tuple[str, ...],
        float,
        ResearchFeatureVector,
        tuple[str, ...],
    ]:
        fv = vec.as_dict()
        policy = self._policy
        present = [c for c in COMPONENTS if vec.has_data(f"val_{c}")]
        missing = policy.missing(present)

        component_specs: list[tuple[str, str, float, str]] = [
            # (field_name, label, weight, interpretation)
            (
                "val_robustness_overall",
                "robustness_overall",
                w.robustness_overall,
                "overall robustness",
            ),
            (
                "val_overfitting_resistance",
                "overfitting_resistance",
                w.overfitting_resistance,
                "overfitting resistance",
            ),
            (
                "val_regime_robustness",
                "regime_robustness",
                w.regime_robustness,
                "regime robustness",
            ),
            (
                "val_walk_forward_consistency",
                "walk_forward_consistency",
                w.walk_forward_consistency,
                "walk-forward consistency",
            ),
            (
                "val_stress_resilience",
                "stress_resilience",
                w.stress_resilience,
                "stress resilience",
            ),
            (
                "val_parameter_stability",
                "parameter_stability",
                w.parameter_stability,
                "parameter stability",
            ),
            (
                "overfit_low_trade_count",
                "trade_reliability",
                w.trade_reliability,
                "trade count reliability",
            ),
        ]

        def counts(label: str) -> bool:
            if missing:
                return False  # not scored: every signal is listed with weight 0
            if label == _ALWAYS_DECIDES:
                return True
            return policy.decides(_FOLLOWS_COMPONENT.get(label, label))

        active_weight_sum = sum(wt for _, label, wt, _ in component_specs if counts(label))
        if active_weight_sum == 0.0 and not missing:
            return 0.0, (), (), 0.0, vec, missing

        components: list[RankingComponent] = []
        composite = 0.0

        for field_name, label, weight, interp in component_specs:
            raw = fv.get(field_name, _NEUTRAL)
            eff_w = weight / active_weight_sum if counts(label) else 0.0
            contrib = eff_w * raw
            composite += contrib
            components.append(
                RankingComponent(
                    name=label,
                    raw_value=raw,
                    normalised_value=raw,
                    weight=eff_w,
                    weighted_contribution=contrib,
                    explanation=f"{interp}: {raw:.3f} × {eff_w:.3f} = {contrib:.4f}",
                )
            )

        composite = _clamp(composite)

        # Deployability: plain average of the deciding components (by default
        # walk-forward consistency and Monte Carlo stability). Not scored when
        # required evidence is missing.
        deploy_signals = [fv.get(f"val_{c}", _NEUTRAL) for c in COMPONENTS if policy.decides(c)]
        deployability = (
            sum(deploy_signals) / len(deploy_signals) if deploy_signals and not missing else 0.0
        )

        weaknesses = self._detect_weaknesses(fv)
        if missing:
            weaknesses = (f"not scored: missing {', '.join(missing)}", *weaknesses)

        return composite, tuple(components), weaknesses, deployability, vec, missing

    def _detect_weaknesses(self, fv: dict[str, float]) -> tuple[str, ...]:
        flags: list[str] = []
        thresholds: list[tuple[str, float, str]] = [
            ("val_walk_forward_consistency", 0.4, "weak walk-forward consistency"),
            ("val_overfitting_resistance", 0.4, "high overfitting risk"),
            ("val_regime_robustness", 0.3, "poor regime robustness"),
            ("val_stress_resilience", 0.3, "low stress resilience"),
            ("overfit_low_trade_count", 0.5, "sparse trade count"),
            ("overfit_train_test_degradation", 0.4, "high train/test degradation"),
            ("regime_is_robust", 0.5, "not regime-robust"),
            ("val_parameter_stability", 0.35, "fragile parameter sensitivity"),
        ]
        for field_name, threshold, message in thresholds:
            if fv.get(field_name, _NEUTRAL) < threshold:
                flags.append(message)
        return tuple(flags)

    @staticmethod
    def _explain(
        composite: float,
        components: tuple[RankingComponent, ...],
        weaknesses: tuple[str, ...],
        deployability: float,
        missing: tuple[str, ...] = (),
    ) -> str:
        if missing:
            return (
                f"Not scored: required evidence missing ({', '.join(missing)}). "
                "Composite and deployability are 0 and do not rank the strategy."
            )
        grade = (
            "excellent"
            if composite >= 0.75
            else "good"
            if composite >= 0.60
            else "borderline"
            if composite >= 0.45
            else "poor"
        )
        top = max(components, key=lambda c: c.weighted_contribution, default=None)
        bottom = min(components, key=lambda c: c.normalised_value, default=None)
        parts = [
            f"Composite={composite:.3f} ({grade}).",
            f"Deployability={deployability:.3f}.",
        ]
        if top:
            parts.append(f"Strongest: {top.name} ({top.normalised_value:.3f}).")
        if bottom and bottom.normalised_value < 0.4:
            parts.append(f"Weakest: {bottom.name} ({bottom.normalised_value:.3f}).")
        if weaknesses:
            parts.append(f"Flags: {'; '.join(weaknesses[:3])}.")
        return " ".join(parts)
