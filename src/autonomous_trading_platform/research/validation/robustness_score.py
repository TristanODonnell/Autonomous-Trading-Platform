"""
Unified, explainable robustness scoring for validated strategy runs.

The overall robustness score is a weighted composite of six components.
Each component is normalised to [0, 1] before weighting.  The default
weights reflect the platform's research philosophy:

  walk_forward_consistency  0.30  — most important: generalises across time
  mc_stability              0.20  — Monte Carlo Sharpe CoV
  regime_robustness         0.20  — works across market regimes
  parameter_stability       0.15  — not a knife-edge parameter fit
  stress_resilience         0.10  — survives adverse scenarios
  overfitting_resistance    0.05  — heuristic overfit indicators

Scores of 0.0 on a disabled component (no data supplied) are handled by
adjusting the effective weight so the overall score remains comparable
across runs where only a subset of validation stages was executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RobustnessWeights:
    walk_forward: float = 0.30
    mc_stability: float = 0.20
    regime: float = 0.20
    parameter_stability: float = 0.15
    stress_resilience: float = 0.10
    overfitting_resistance: float = 0.05

    def __post_init__(self) -> None:
        for name in (
            "walk_forward",
            "mc_stability",
            "regime",
            "parameter_stability",
            "stress_resilience",
            "overfitting_resistance",
        ):
            v = getattr(self, name)
            if v < 0.0:
                raise ValueError(f"RobustnessWeights.{name} must be >= 0, got {v}")


@dataclass(frozen=True)
class RobustnessScore:
    """
    Composite robustness score for a single validated strategy.

    All component scores are in [0, 1].  The overall score is a weighted
    sum renormalised so that disabled components (None → excluded from
    computation) do not dilute the overall figure.
    """

    overall: float
    walk_forward_consistency: float | None
    mc_stability: float | None
    regime_robustness: float | None
    parameter_stability: float | None
    stress_resilience: float | None
    overfitting_resistance: float | None
    explanation: list[str] = field(default_factory=list)
    weights_used: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": round(self.overall, 4),
            "components": {
                "walk_forward_consistency": _fmt(self.walk_forward_consistency),
                "mc_stability": _fmt(self.mc_stability),
                "regime_robustness": _fmt(self.regime_robustness),
                "parameter_stability": _fmt(self.parameter_stability),
                "stress_resilience": _fmt(self.stress_resilience),
                "overfitting_resistance": _fmt(self.overfitting_resistance),
            },
            "weights_used": {k: round(v, 4) for k, v in self.weights_used.items()},
            "explanation": self.explanation,
        }


def _fmt(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _cv_to_stability(cv: float) -> float:
    """Convert coefficient-of-variation to a stability score in [0, 1].

    CV = 0 → stability = 1.0 (perfectly stable)
    CV = 1 → stability = 0.5
    CV >= 2 → stability → 0 (very unstable)
    """
    return _clamp(1.0 / (1.0 + cv))


class RobustnessScoreBuilder:
    """
    Fluent builder for constructing a RobustnessScore from validation
    stage outputs.

    Only supply the components you have data for; others are treated as
    absent and their weights are redistributed.

    Usage
    -----
    score = (
        RobustnessScoreBuilder()
        .with_walk_forward(folds_passed=8, total_folds=10)
        .with_monte_carlo(sharpe_cv=0.25)
        .with_regime(is_regime_robust=True, worst_regime_sharpe=0.3)
        .with_stress(survival_rate=0.75)
        .with_overfitting(overfitting_probability=0.2)
        .build()
    )
    """

    def __init__(self, weights: RobustnessWeights | None = None) -> None:
        self._weights = weights or RobustnessWeights()
        self._components: dict[str, float] = {}
        self._explanation: list[str] = []

    # ------------------------------------------------------------------
    # Component setters
    # ------------------------------------------------------------------

    def with_walk_forward(
        self,
        *,
        folds_passed: int,
        total_folds: int,
        train_test_degradation: float = 0.0,
    ) -> RobustnessScoreBuilder:
        if total_folds <= 0:
            return self
        consistency = folds_passed / total_folds
        # Penalise large train/test degradation (cap at 0.5 penalty)
        degradation_penalty = _clamp(train_test_degradation / 2.0)
        score = _clamp(consistency - degradation_penalty)
        self._components["walk_forward_consistency"] = score
        self._explanation.append(
            f"walk_forward: {folds_passed}/{total_folds} folds passed "
            f"(consistency={consistency:.2f}, degradation_penalty={degradation_penalty:.2f}) → {score:.3f}"
        )
        return self

    def with_monte_carlo(self, *, sharpe_cv: float) -> RobustnessScoreBuilder:
        score = _cv_to_stability(sharpe_cv)
        self._components["mc_stability"] = score
        self._explanation.append(
            f"mc_stability: Sharpe CoV={sharpe_cv:.3f} → stability={score:.3f}"
        )
        return self

    def with_regime(
        self,
        *,
        is_regime_robust: bool,
        worst_regime_sharpe: float,
        overall_sensitivity: float = 0.0,
    ) -> RobustnessScoreBuilder:
        # Base: 0.5 for robust, 0.25 for non-robust
        base = 0.5 if is_regime_robust else 0.25
        # Add credit for positive worst-regime Sharpe (max bonus 0.3)
        sharpe_bonus = _clamp(worst_regime_sharpe / 3.0) * 0.3
        # Penalise high sensitivity (max penalty 0.2)
        sensitivity_penalty = _clamp(overall_sensitivity / 5.0) * 0.2
        score = _clamp(base + sharpe_bonus - sensitivity_penalty)
        self._components["regime_robustness"] = score
        self._explanation.append(
            f"regime: robust={is_regime_robust} worst_sharpe={worst_regime_sharpe:.3f} "
            f"sensitivity={overall_sensitivity:.3f} → {score:.3f}"
        )
        return self

    def with_parameter_stability(self, *, overall_stability_score: float) -> RobustnessScoreBuilder:
        score = _clamp(overall_stability_score)
        self._components["parameter_stability"] = score
        self._explanation.append(f"parameter_stability: {score:.3f}")
        return self

    def with_stress(self, *, survival_rate: float) -> RobustnessScoreBuilder:
        score = _clamp(survival_rate)
        self._components["stress_resilience"] = score
        self._explanation.append(
            f"stress_resilience: {survival_rate:.0%} scenarios survived → {score:.3f}"
        )
        return self

    def with_overfitting(self, *, overfitting_probability: float) -> RobustnessScoreBuilder:
        score = _clamp(1.0 - overfitting_probability)
        self._components["overfitting_resistance"] = score
        self._explanation.append(
            f"overfitting_resistance: P(overfit)={overfitting_probability:.3f} → {score:.3f}"
        )
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> RobustnessScore:
        w = self._weights
        component_map = {
            "walk_forward_consistency": w.walk_forward,
            "mc_stability": w.mc_stability,
            "regime_robustness": w.regime,
            "parameter_stability": w.parameter_stability,
            "stress_resilience": w.stress_resilience,
            "overfitting_resistance": w.overfitting_resistance,
        }

        active_weight_sum = sum(
            weight for key, weight in component_map.items() if key in self._components
        )

        if active_weight_sum == 0.0:
            overall = 0.0
            effective_weights: dict[str, float] = {}
        else:
            effective_weights = {
                key: weight / active_weight_sum
                for key, weight in component_map.items()
                if key in self._components
            }
            overall = sum(
                effective_weights[key] * self._components[key] for key in effective_weights
            )
            overall = _clamp(overall)

        return RobustnessScore(
            overall=overall,
            walk_forward_consistency=self._components.get("walk_forward_consistency"),
            mc_stability=self._components.get("mc_stability"),
            regime_robustness=self._components.get("regime_robustness"),
            parameter_stability=self._components.get("parameter_stability"),
            stress_resilience=self._components.get("stress_resilience"),
            overfitting_resistance=self._components.get("overfitting_resistance"),
            explanation=list(self._explanation),
            weights_used=effective_weights,
        )
