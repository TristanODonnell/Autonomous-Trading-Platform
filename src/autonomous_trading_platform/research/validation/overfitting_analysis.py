"""
Deterministic overfitting detection via explainable heuristics.

Each indicator scores a specific overfitting signal in [0, 1]:
  1  train_test_degradation  — train Sharpe >> test Sharpe consistently
  2  fold_instability        — high CoV of test Sharpe across folds
  3  mc_instability          — high CoV of Sharpe across Monte Carlo runs
  4  regime_concentration    — performance only works in a narrow regime
  5  low_trade_count         — too few trades for statistical significance
  6  narrow_period_alpha     — most returns concentrated in a few bars
  7  parameter_fragility     — small param changes cause large perf changes

overfitting_probability aggregates indicators with configurable weights.
A probability of 0.0 is ideal; >= 0.6 is a red flag.

Design principles:
  - All heuristics are deterministic and explainable
  - No black-box ML models
  - Each indicator can be computed independently (None → excluded)
  - Results are fully decomposable for human review
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
        StrategyRegimeProfile,
    )
    from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
        MonteCarloAggregation,
    )
    from autonomous_trading_platform.research.validation.walk_forward_validation import (
        WalkForwardValidationResult,
    )


_EPSILON = 1e-9
_MIN_RELIABLE_TRADE_COUNT = 30
_NARROW_ALPHA_TOP_FRACTION = 0.10  # top 10% of bars by absolute return


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


@dataclass(frozen=True)
class OverfittingIndicators:
    """Raw indicator values before aggregation."""

    train_test_degradation: float | None  # [0, 1]; >0.5 suspicious
    fold_instability: float | None  # CoV of test Sharpe; high = unstable
    mc_instability: float | None  # CoV of MC Sharpe; high = fragile
    regime_concentration: float | None  # [0, 1]; low = concentrated in one regime
    low_trade_count_flag: bool | None  # True when trade count < min threshold
    narrow_period_alpha: float | None  # [0, 1]; fraction of return from top-N bars
    parameter_fragility: float | None  # [0, 1]; from parameter sensitivity analysis

    def active(self) -> dict[str, float]:
        """Return only indicators that have a value (not None)."""
        mapping = {
            "train_test_degradation": self.train_test_degradation,
            "fold_instability": self.fold_instability,
            "mc_instability": self.mc_instability,
            "regime_concentration": (
                None
                if self.regime_concentration is None
                else 1.0 - self.regime_concentration  # invert: low concentration = high risk
            ),
            "low_trade_count": (
                None
                if self.low_trade_count_flag is None
                else (1.0 if self.low_trade_count_flag else 0.0)
            ),
            "narrow_period_alpha": self.narrow_period_alpha,
            "parameter_fragility": self.parameter_fragility,
        }
        return {k: v for k, v in mapping.items() if v is not None}


@dataclass(frozen=True)
class OverfittingAnalysisResult:
    """
    Overfitting detection outcome for a single strategy.

    overfitting_probability is in [0, 1]:
      0.0 – 0.3   unlikely overfitted
      0.3 – 0.6   moderate concern
      0.6 – 1.0   high overfitting risk
    """

    strategy_id: str
    overfitting_probability: float
    resistance_score: float  # 1 − probability
    indicators: OverfittingIndicators
    most_suspicious: list[str]  # top 3 indicator names by score
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "overfitting_probability": round(self.overfitting_probability, 4),
            "resistance_score": round(self.resistance_score, 4),
            "indicators": {
                "train_test_degradation": self.indicators.train_test_degradation,
                "fold_instability": self.indicators.fold_instability,
                "mc_instability": self.indicators.mc_instability,
                "regime_concentration": self.indicators.regime_concentration,
                "low_trade_count_flag": self.indicators.low_trade_count_flag,
                "narrow_period_alpha": self.indicators.narrow_period_alpha,
                "parameter_fragility": self.indicators.parameter_fragility,
            },
            "most_suspicious": self.most_suspicious,
            "warnings": self.warnings,
        }


def _cv(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_abs = abs(statistics.mean(values))
    if mean_abs < _EPSILON:
        return 0.0
    return statistics.stdev(values) / mean_abs


def _narrow_period_alpha(
    equity_curve: pd.DataFrame, top_fraction: float = _NARROW_ALPHA_TOP_FRACTION
) -> float:
    """Fraction of total return attributable to the top-N% of bars by absolute return."""
    if equity_curve is None or equity_curve.empty:
        return 0.0
    eq = equity_curve.sort_values("timestamp")["equity"].to_numpy(dtype=np.float64)
    if len(eq) < 2:
        return 0.0
    returns = np.diff(eq) / (eq[:-1] + _EPSILON)
    abs_returns = np.abs(returns)
    total_abs = abs_returns.sum()
    if total_abs < _EPSILON:
        return 0.0
    threshold = np.percentile(abs_returns, (1.0 - top_fraction) * 100)
    top_contribution = abs_returns[abs_returns >= threshold].sum()
    return float(_clamp(top_contribution / total_abs))


class OverfittingAnalyzer:
    """
    Detects overfitting signals using explainable heuristics.

    All inputs are optional — the analyzer uses whatever data is available
    and adjusts the aggregation accordingly.

    Parameters
    ----------
    min_trade_count
        Strategies with fewer trades than this are flagged as low-trade-count.
    indicator_weights
        Custom weights for the aggregation.  Keys must match indicator names
        in OverfittingIndicators.active().
    """

    _DEFAULT_WEIGHTS = {
        "train_test_degradation": 0.30,
        "fold_instability": 0.20,
        "mc_instability": 0.20,
        "regime_concentration": 0.15,
        "low_trade_count": 0.05,
        "narrow_period_alpha": 0.05,
        "parameter_fragility": 0.05,
    }

    def __init__(
        self,
        *,
        min_trade_count: int = _MIN_RELIABLE_TRADE_COUNT,
        indicator_weights: dict[str, float] | None = None,
    ) -> None:
        self._min_trade_count = min_trade_count
        self._weights = indicator_weights or dict(self._DEFAULT_WEIGHTS)

    def analyze(
        self,
        *,
        strategy_id: str,
        wf_result: WalkForwardValidationResult | None = None,
        mc_aggregation: MonteCarloAggregation | None = None,
        regime_profile: StrategyRegimeProfile | None = None,
        trade_count: int | None = None,
        equity_curve: pd.DataFrame | None = None,
        sensitivity_score: float | None = None,
    ) -> OverfittingAnalysisResult:
        warnings: list[str] = []

        # 1. Train/test degradation
        ttd: float | None = None
        if wf_result is not None:
            raw_deg = wf_result.train_test_degradation
            ttd = _clamp(raw_deg / 2.0)  # normalise: 2.0 degradation = fully suspicious
            if ttd > 0.5:
                warnings.append(
                    f"Train/test Sharpe degradation is {wf_result.train_test_degradation:.2f} "
                    f"(train={wf_result.avg_train_sharpe:.2f}, test={wf_result.avg_test_sharpe:.2f})."
                )

        # 2. Fold instability
        fi: float | None = None
        if wf_result is not None:
            fi = _clamp(wf_result.fold_sharpe_cv / 2.0)  # normalise: CoV=2 = fully suspicious
            if fi > 0.5:
                warnings.append(
                    f"Fold test-Sharpe CoV is {wf_result.fold_sharpe_cv:.2f} — "
                    "large fold-to-fold performance swings."
                )

        # 3. Monte Carlo instability
        mc: float | None = None
        if mc_aggregation is not None:
            mc_cv = mc_aggregation.sharpe_dist.coefficient_of_variation
            mc = _clamp(mc_cv / 2.0)
            if mc > 0.5:
                warnings.append(
                    f"Monte Carlo Sharpe CoV is {mc_cv:.2f} — "
                    "performance sensitive to random execution ordering."
                )

        # 4. Regime concentration
        rc: float | None = None
        if regime_profile is not None:
            dims = [
                regime_profile.by_trend,
                regime_profile.by_volatility,
                regime_profile.by_liquidity,
                regime_profile.by_mean_reversion,
                regime_profile.by_risk,
            ]
            positive_regime_fractions: list[float] = []
            for dim_summary in dims:
                buckets = dim_summary.metrics_by_label
                if not buckets:
                    continue
                n_positive = sum(
                    1 for m in buckets.values() if m.sharpe is not None and m.sharpe > 0
                )
                positive_regime_fractions.append(n_positive / len(buckets))
            if positive_regime_fractions:
                rc = statistics.mean(positive_regime_fractions)
                if rc < 0.4:
                    warnings.append(
                        f"Strategy earns positive Sharpe in only {rc:.0%} of regime buckets — "
                        "concentrated regime dependence."
                    )

        # 5. Low trade count
        low_trade: bool | None = None
        if trade_count is not None:
            low_trade = trade_count < self._min_trade_count
            if low_trade:
                warnings.append(
                    f"Only {trade_count} trades — below minimum {self._min_trade_count} "
                    "for statistical reliability."
                )

        # 6. Narrow-period alpha
        npa: float | None = None
        if equity_curve is not None and not equity_curve.empty:
            npa = _narrow_period_alpha(equity_curve)
            if npa > 0.7:
                warnings.append(
                    f"Top {int(_NARROW_ALPHA_TOP_FRACTION * 100)}% of bars account for "
                    f"{npa:.0%} of absolute return — performance may depend on a few lucky bars."
                )

        # 7. Parameter fragility
        pf: float | None = None
        if sensitivity_score is not None:
            pf = _clamp(sensitivity_score)
            if pf > 0.6:
                warnings.append(
                    f"Parameter sensitivity score is {pf:.2f} — "
                    "performance is fragile to parameter changes."
                )

        indicators = OverfittingIndicators(
            train_test_degradation=ttd,
            fold_instability=fi,
            mc_instability=mc,
            regime_concentration=rc,
            low_trade_count_flag=low_trade,
            narrow_period_alpha=npa,
            parameter_fragility=pf,
        )

        active = indicators.active()

        if not active:
            overfit_prob = 0.0
        else:
            active_weight_sum = sum(self._weights.get(k, 0.05) for k in active)
            if active_weight_sum == 0.0:
                overfit_prob = 0.0
            else:
                overfit_prob = sum(
                    (self._weights.get(k, 0.05) / active_weight_sum) * v for k, v in active.items()
                )

        overfit_prob = _clamp(overfit_prob)
        resistance = 1.0 - overfit_prob

        most_suspicious = sorted(active, key=lambda k: -active[k])[:3]

        if overfit_prob >= 0.6:
            warnings.append(
                f"High overfitting probability ({overfit_prob:.2f}) — "
                "strategy may not generalise beyond the training data."
            )

        return OverfittingAnalysisResult(
            strategy_id=strategy_id,
            overfitting_probability=overfit_prob,
            resistance_score=resistance,
            indicators=indicators,
            most_suspicious=most_suspicious,
            warnings=warnings,
        )
