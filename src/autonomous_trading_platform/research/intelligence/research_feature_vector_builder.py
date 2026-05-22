"""
Canonical research feature vector builder (TASK-2.5).

Consumes existing ValidationSummary and StrategyRegimeProfile artifacts to
produce a stable, normalised numeric feature vector for each strategy
candidate.  The vector is used by the ranking, clustering, robustness
estimation, and overfitting estimation services.

Design principles
-----------------
- Deterministic: same inputs → same vector, always
- Normalised: all fields mapped to [0, 1] via documented formulas
- Explainable: every field carries a source tag and description
- Stable ordering: fields are defined in a fixed order (validation →
  overfitting → walk-forward → stress → regime → metadata)
- Graceful degradation: missing inputs produce 0.5 (neutral) defaults,
  tracked via data_completeness
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_EPSILON = 1e-9
_NEUTRAL = 0.5  # default when a field has no data


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _normalise_sharpe(sharpe: float) -> float:
    """Map Sharpe ratio to [0,1].  0 → 0.5; +3 → ~1.0; -3 → ~0.0."""
    return _clamp(0.5 + sharpe / 6.0)


def _normalise_regime_robustness(worst_regime_sharpe: float) -> float:
    """Map worst-regime Sharpe to [0,1].  0 → 0.5; +2 → 1.0; -2 → 0.0."""
    return _clamp(0.5 + worst_regime_sharpe / 4.0)


def _normalise_sensitivity(sensitivity: float) -> float:
    """Invert: lower sensitivity → higher score.  0 → 1.0; 2 → 0.0."""
    return _clamp(1.0 - sensitivity / 2.0)


# ---------------------------------------------------------------------------
# Field definitions (stable ordering)
# ---------------------------------------------------------------------------

#: Canonical field names in stable insertion order.
FIELD_NAMES: tuple[str, ...] = (
    # --- Validation robustness components (7) ---
    "val_robustness_overall",
    "val_walk_forward_consistency",
    "val_mc_stability",
    "val_regime_robustness",
    "val_parameter_stability",
    "val_stress_resilience",
    "val_overfitting_resistance",
    # --- Overfitting indicators (9) ---
    "overfit_probability",
    "overfit_resistance",
    "overfit_train_test_degradation",
    "overfit_fold_instability",
    "overfit_mc_instability",
    "overfit_regime_concentration",
    "overfit_narrow_period_alpha",
    "overfit_parameter_fragility",
    "overfit_low_trade_count",
    # --- Walk-forward (4) ---
    "wf_fold_consistency",
    "wf_avg_test_sharpe_norm",
    "wf_train_test_degradation",
    "wf_fold_sharpe_stability",
    # --- Stress (2) ---
    "stress_survival_rate",
    "stress_sharpe_resilience",
    # --- Regime sensitivity (7) ---
    "regime_is_robust",
    "regime_sensitivity_norm",
    "regime_trend_robustness_norm",
    "regime_volatility_robustness_norm",
    "regime_liquidity_robustness_norm",
    "regime_mean_reversion_robustness_norm",
    "regime_risk_robustness_norm",
    # --- Strategy metadata (6) ---
    "family_momentum",
    "family_mean_reversion",
    "family_trend",
    "family_factor",
    "family_composite",
    "n_parameters_norm",
)


@dataclass(frozen=True)
class FeatureField:
    """Single named field in a ResearchFeatureVector."""

    name: str
    value: float  # normalised [0, 1]
    description: str
    source: str  # "validation" | "overfitting" | "walk_forward" | "stress" | "regime" | "metadata"
    has_data: bool  # False when the value is a neutral default


@dataclass(frozen=True)
class ResearchFeatureVector:
    """
    Normalised numeric feature representation of a strategy candidate.

    All field values are in [0, 1].  Higher values are consistently better
    (robustness, resistance, etc.).  Missing data is represented by the
    neutral value 0.5 and tracked in data_completeness.
    """

    strategy_id: str
    experiment_id: str
    dataset_version: str
    fields: tuple[FeatureField, ...]
    config_hash: str  # deterministic SHA-256 of input signal values
    computed_at: datetime
    data_completeness: float  # fraction of fields that have real data

    @property
    def as_array(self) -> list[float]:
        """Stable-ordered numeric vector for clustering / distance computations."""
        return [f.value for f in self.fields]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def as_dict(self) -> dict[str, float]:
        return {f.name: round(f.value, 6) for f in self.fields}

    def get(self, name: str, default: float = _NEUTRAL) -> float:
        for f in self.fields:
            if f.name == name:
                return f.value
        return default


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ResearchFeatureVectorBuilder:
    """
    Builds ResearchFeatureVector from existing validation and regime artifacts.

    Inputs are all optional — the builder degrades gracefully when only
    partial data is available, using neutral (0.5) values for missing fields.
    """

    def build(
        self,
        *,
        strategy_id: str,
        experiment_id: str,
        dataset_version: str,
        validation_summary: Any | None = None,  # ValidationSummary
        regime_profile: Any | None = None,  # StrategyRegimeProfile
        strategy_family: str | None = None,  # StrategyFamily value string
        n_parameters: int | None = None,
    ) -> ResearchFeatureVector:
        raw: dict[str, tuple[float, bool]] = {}  # name → (value, has_data)

        self._add_validation_fields(raw, validation_summary)
        self._add_overfitting_fields(raw, validation_summary)
        self._add_walk_forward_fields(raw, validation_summary)
        self._add_stress_fields(raw, validation_summary)
        self._add_regime_fields(raw, regime_profile)
        self._add_metadata_fields(raw, strategy_family, n_parameters)

        fields = tuple(
            FeatureField(
                name=name,
                value=raw[name][0],
                description=_FIELD_DESCRIPTIONS.get(name, ""),
                source=_FIELD_SOURCES.get(name, "unknown"),
                has_data=raw[name][1],
            )
            for name in FIELD_NAMES
            if name in raw
        )

        fields_with_data = sum(1 for f in fields if f.has_data)
        data_completeness = fields_with_data / len(fields) if fields else 0.0

        config_hash = _compute_hash(strategy_id, experiment_id, dataset_version, fields)

        return ResearchFeatureVector(
            strategy_id=strategy_id,
            experiment_id=experiment_id,
            dataset_version=dataset_version,
            fields=fields,
            config_hash=config_hash,
            computed_at=datetime.now(UTC),
            data_completeness=data_completeness,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_validation_fields(
        self,
        raw: dict[str, tuple[float, bool]],
        summary: Any | None,
    ) -> None:
        rs = None if summary is None else summary.robustness_score

        def _rs(attr: str) -> tuple[float, bool]:
            if rs is None:
                return (_NEUTRAL, False)
            v = getattr(rs, attr, None)
            return (v, True) if v is not None else (_NEUTRAL, False)

        raw["val_robustness_overall"] = (rs.overall, True) if rs is not None else (_NEUTRAL, False)
        raw["val_walk_forward_consistency"] = _rs("walk_forward_consistency")
        raw["val_mc_stability"] = _rs("mc_stability")
        raw["val_regime_robustness"] = _rs("regime_robustness")
        raw["val_parameter_stability"] = _rs("parameter_stability")
        raw["val_stress_resilience"] = _rs("stress_resilience")
        raw["val_overfitting_resistance"] = _rs("overfitting_resistance")

    def _add_overfitting_fields(
        self,
        raw: dict[str, tuple[float, bool]],
        summary: Any | None,
    ) -> None:
        overfit_stage = None
        if summary is not None:
            overfit_stage = summary.stage_results.get("overfitting")

        if overfit_stage is None:
            for name in (
                "overfit_probability",
                "overfit_resistance",
                "overfit_train_test_degradation",
                "overfit_fold_instability",
                "overfit_mc_instability",
                "overfit_regime_concentration",
                "overfit_narrow_period_alpha",
                "overfit_parameter_fragility",
                "overfit_low_trade_count",
            ):
                raw[name] = (_NEUTRAL, False)
            return

        s = overfit_stage.summary
        prob = s.get("overfitting_probability", None)
        resistance = s.get("resistance_score", None)
        indicators = s.get("indicators", {})

        raw["overfit_probability"] = (
            (_clamp(1.0 - (prob or 0.0)), True) if prob is not None else (_NEUTRAL, False)
        )
        raw["overfit_resistance"] = (
            (_clamp(resistance), True) if resistance is not None else (_NEUTRAL, False)
        )

        def _ind(key: str, invert: bool = False) -> tuple[float, bool]:
            v = indicators.get(key)
            if v is None:
                return (_NEUTRAL, False)
            if isinstance(v, bool):
                val = 1.0 - (1.0 if v else 0.0)  # low_trade_count: True=bad → 0.0
                return (val, True)
            normed = _clamp(float(v))
            return (_clamp(1.0 - normed) if invert else normed, True)

        # degradation/instability: high = bad → invert for "goodness"
        raw["overfit_train_test_degradation"] = _ind("train_test_degradation", invert=True)
        raw["overfit_fold_instability"] = _ind("fold_instability", invert=True)
        raw["overfit_mc_instability"] = _ind("mc_instability", invert=True)
        # regime_concentration: high fraction = good (many positive regimes)
        raw["overfit_regime_concentration"] = _ind("regime_concentration", invert=False)
        # narrow_period_alpha: high = bad → invert
        raw["overfit_narrow_period_alpha"] = _ind("narrow_period_alpha", invert=True)
        # parameter_fragility: high = bad → invert
        raw["overfit_parameter_fragility"] = _ind("parameter_fragility", invert=True)
        # low_trade_count_flag: True = bad
        raw["overfit_low_trade_count"] = _ind("low_trade_count_flag", invert=False)

    def _add_walk_forward_fields(
        self,
        raw: dict[str, tuple[float, bool]],
        summary: Any | None,
    ) -> None:
        wf_stage = None
        if summary is not None:
            wf_stage = summary.stage_results.get("walk_forward")

        if wf_stage is None:
            raw["wf_fold_consistency"] = (_NEUTRAL, False)
            raw["wf_avg_test_sharpe_norm"] = (_NEUTRAL, False)
            raw["wf_train_test_degradation"] = (_NEUTRAL, False)
            raw["wf_fold_sharpe_stability"] = (_NEUTRAL, False)
            return

        s = wf_stage.summary
        raw["wf_fold_consistency"] = (
            _clamp(s.get("fold_consistency", _NEUTRAL)),
            True,
        )
        avg_test_sharpe = s.get("avg_test_sharpe")
        raw["wf_avg_test_sharpe_norm"] = (
            (_normalise_sharpe(avg_test_sharpe), True)
            if avg_test_sharpe is not None
            else (_NEUTRAL, False)
        )
        ttd = s.get("train_test_degradation")
        # degradation: high = bad → invert
        raw["wf_train_test_degradation"] = (
            (_clamp(1.0 - _clamp(ttd / 2.0)), True) if ttd is not None else (_NEUTRAL, False)
        )
        fold_cv = s.get("fold_sharpe_cv")
        # fold_sharpe_stability = 1 - clamp(cv)
        raw["wf_fold_sharpe_stability"] = (
            (_clamp(1.0 / (1.0 + fold_cv)), True) if fold_cv is not None else (_NEUTRAL, False)
        )

    def _add_stress_fields(
        self,
        raw: dict[str, tuple[float, bool]],
        summary: Any | None,
    ) -> None:
        stress_stage = None
        if summary is not None:
            stress_stage = summary.stage_results.get("stress_test")

        if stress_stage is None:
            raw["stress_survival_rate"] = (_NEUTRAL, False)
            raw["stress_sharpe_resilience"] = (_NEUTRAL, False)
            return

        s = stress_stage.summary
        sr = s.get("survival_rate")
        raw["stress_survival_rate"] = (_clamp(sr), True) if sr is not None else (_NEUTRAL, False)
        asd = s.get("avg_sharpe_degradation")
        # avg_sharpe_degradation: 0 = no degradation (good), 1 = full loss (bad)
        raw["stress_sharpe_resilience"] = (
            (_clamp(1.0 - asd), True) if asd is not None else (_NEUTRAL, False)
        )

    def _add_regime_fields(
        self,
        raw: dict[str, tuple[float, bool]],
        profile: Any | None,  # StrategyRegimeProfile
    ) -> None:
        if profile is None:
            for name in (
                "regime_is_robust",
                "regime_sensitivity_norm",
                "regime_trend_robustness_norm",
                "regime_volatility_robustness_norm",
                "regime_liquidity_robustness_norm",
                "regime_mean_reversion_robustness_norm",
                "regime_risk_robustness_norm",
            ):
                raw[name] = (_NEUTRAL, False)
            return

        raw["regime_is_robust"] = (1.0 if profile.is_regime_robust else 0.0, True)
        raw["regime_sensitivity_norm"] = (
            _normalise_sensitivity(profile.overall_sensitivity),
            True,
        )

        dim_map = {
            "regime_trend_robustness_norm": profile.by_trend,
            "regime_volatility_robustness_norm": profile.by_volatility,
            "regime_liquidity_robustness_norm": profile.by_liquidity,
            "regime_mean_reversion_robustness_norm": profile.by_mean_reversion,
            "regime_risk_robustness_norm": profile.by_risk,
        }
        for field_name, dim_summary in dim_map.items():
            rr = dim_summary.sensitivity.regime_robustness
            raw[field_name] = (_normalise_regime_robustness(rr), True)

    def _add_metadata_fields(
        self,
        raw: dict[str, tuple[float, bool]],
        strategy_family: str | None,
        n_parameters: int | None,
    ) -> None:
        families = ("momentum", "mean_reversion", "trend", "factor", "composite")
        fam = (strategy_family or "").lower()
        for fam_name in families:
            key = f"family_{fam_name}"
            raw[key] = (1.0 if fam == fam_name else 0.0, strategy_family is not None)

        if n_parameters is not None:
            raw["n_parameters_norm"] = (_clamp(n_parameters / 10.0), True)
        else:
            raw["n_parameters_norm"] = (_NEUTRAL, False)


# ---------------------------------------------------------------------------
# Metadata tables
# ---------------------------------------------------------------------------

_FIELD_DESCRIPTIONS: dict[str, str] = {
    "val_robustness_overall": "Overall robustness score from ValidationSummary",
    "val_walk_forward_consistency": "Walk-forward fold consistency component",
    "val_mc_stability": "Monte Carlo Sharpe stability component",
    "val_regime_robustness": "Regime robustness component from validation",
    "val_parameter_stability": "Parameter stability component",
    "val_stress_resilience": "Stress scenario survival component",
    "val_overfitting_resistance": "Overfitting resistance component",
    "overfit_probability": "1 - overfit_probability (higher = less overfit)",
    "overfit_resistance": "Resistance score from OverfittingAnalyzer",
    "overfit_train_test_degradation": "1 - normalised T/T degradation (higher = lower degradation)",
    "overfit_fold_instability": "1 - fold CoV (higher = more stable folds)",
    "overfit_mc_instability": "1 - MC CoV (higher = more stable MC)",
    "overfit_regime_concentration": "Fraction of positive-Sharpe regime buckets",
    "overfit_narrow_period_alpha": "1 - narrow_period_alpha (higher = returns spread evenly)",
    "overfit_parameter_fragility": "1 - parameter_fragility (higher = more stable params)",
    "overfit_low_trade_count": "1 if trade count OK, 0 if below minimum",
    "wf_fold_consistency": "Fraction of walk-forward folds that passed",
    "wf_avg_test_sharpe_norm": "Normalised average test-set Sharpe (0.5 = 0 Sharpe)",
    "wf_train_test_degradation": "1 - normalised degradation (higher = less degradation)",
    "wf_fold_sharpe_stability": "Stability of test Sharpe across folds",
    "stress_survival_rate": "Fraction of stress scenarios survived",
    "stress_sharpe_resilience": "1 - avg Sharpe degradation under stress",
    "regime_is_robust": "1.0 if strategy is regime-robust, else 0.0",
    "regime_sensitivity_norm": "1 - normalised overall regime sensitivity",
    "regime_trend_robustness_norm": "Normalised worst-regime Sharpe (trend dimension)",
    "regime_volatility_robustness_norm": "Normalised worst-regime Sharpe (volatility dimension)",
    "regime_liquidity_robustness_norm": "Normalised worst-regime Sharpe (liquidity dimension)",
    "regime_mean_reversion_robustness_norm": "Normalised worst-regime Sharpe (mean-reversion dimension)",
    "regime_risk_robustness_norm": "Normalised worst-regime Sharpe (risk dimension)",
    "family_momentum": "1 if strategy family is momentum",
    "family_mean_reversion": "1 if strategy family is mean_reversion",
    "family_trend": "1 if strategy family is trend",
    "family_factor": "1 if strategy family is factor",
    "family_composite": "1 if strategy family is composite",
    "n_parameters_norm": "Number of parameters normalised by 10 (capped at 1.0)",
}

_FIELD_SOURCES: dict[str, str] = {
    **{
        k: "validation"
        for k in (
            "val_robustness_overall",
            "val_walk_forward_consistency",
            "val_mc_stability",
            "val_regime_robustness",
            "val_parameter_stability",
            "val_stress_resilience",
            "val_overfitting_resistance",
        )
    },
    **{
        k: "overfitting"
        for k in (
            "overfit_probability",
            "overfit_resistance",
            "overfit_train_test_degradation",
            "overfit_fold_instability",
            "overfit_mc_instability",
            "overfit_regime_concentration",
            "overfit_narrow_period_alpha",
            "overfit_parameter_fragility",
            "overfit_low_trade_count",
        )
    },
    **{
        k: "walk_forward"
        for k in (
            "wf_fold_consistency",
            "wf_avg_test_sharpe_norm",
            "wf_train_test_degradation",
            "wf_fold_sharpe_stability",
        )
    },
    **{k: "stress" for k in ("stress_survival_rate", "stress_sharpe_resilience")},
    **{
        k: "regime"
        for k in (
            "regime_is_robust",
            "regime_sensitivity_norm",
            "regime_trend_robustness_norm",
            "regime_volatility_robustness_norm",
            "regime_liquidity_robustness_norm",
            "regime_mean_reversion_robustness_norm",
            "regime_risk_robustness_norm",
        )
    },
    **{
        k: "metadata"
        for k in (
            "family_momentum",
            "family_mean_reversion",
            "family_trend",
            "family_factor",
            "family_composite",
            "n_parameters_norm",
        )
    },
}


def _compute_hash(
    strategy_id: str,
    experiment_id: str,
    dataset_version: str,
    fields: tuple[FeatureField, ...],
) -> str:
    payload = json.dumps(
        {
            "strategy_id": strategy_id,
            "experiment_id": experiment_id,
            "dataset_version": dataset_version,
            "fields": [(f.name, round(f.value, 8)) for f in fields],
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]
