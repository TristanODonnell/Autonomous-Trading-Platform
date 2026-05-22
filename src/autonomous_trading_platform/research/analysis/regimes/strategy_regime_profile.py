from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from autonomous_trading_platform.research.analysis.regimes.regime_metrics import (
    RegimeConditionedMetrics,
)

_MIN_BARS_FOR_SENSITIVITY = 10


@dataclass(frozen=True)
class RegimeSensitivityScore:
    dimension: str
    sharpe_std: float
    sharpe_range: float
    best_regime: str | None
    worst_regime: str | None
    regime_robustness: float


@dataclass(frozen=True)
class RegimePerformanceSummary:
    dimension: str
    metrics_by_label: dict[str, RegimeConditionedMetrics]
    sensitivity: RegimeSensitivityScore


@dataclass(frozen=True)
class StrategyRegimeProfile:
    strategy_id: str
    run_id: str
    experiment_id: str
    by_trend: RegimePerformanceSummary
    by_volatility: RegimePerformanceSummary
    by_liquidity: RegimePerformanceSummary
    by_mean_reversion: RegimePerformanceSummary
    by_risk: RegimePerformanceSummary
    overall_sensitivity: float
    is_regime_robust: bool


def build_sensitivity_score(
    dimension: str,
    metrics_by_label: dict[str, RegimeConditionedMetrics],
) -> RegimeSensitivityScore:
    sharpe_map: dict[str, float] = {
        label: m.sharpe
        for label, m in metrics_by_label.items()
        if m.sharpe is not None and m.bar_count >= _MIN_BARS_FOR_SENSITIVITY
    }

    if not sharpe_map:
        return RegimeSensitivityScore(
            dimension=dimension,
            sharpe_std=0.0,
            sharpe_range=0.0,
            best_regime=None,
            worst_regime=None,
            regime_robustness=0.0,
        )

    vals = list(sharpe_map.values())
    best = max(sharpe_map, key=lambda k: sharpe_map[k])
    worst = min(sharpe_map, key=lambda k: sharpe_map[k])

    return RegimeSensitivityScore(
        dimension=dimension,
        sharpe_std=float(np.std(vals, ddof=0)) if len(vals) > 1 else 0.0,
        sharpe_range=max(vals) - min(vals),
        best_regime=best,
        worst_regime=worst,
        regime_robustness=min(vals),
    )


def build_performance_summary(
    dimension: str,
    metrics_by_label: dict[str, RegimeConditionedMetrics],
) -> RegimePerformanceSummary:
    return RegimePerformanceSummary(
        dimension=dimension,
        metrics_by_label=metrics_by_label,
        sensitivity=build_sensitivity_score(dimension, metrics_by_label),
    )


def build_strategy_regime_profile(
    *,
    strategy_id: str,
    run_id: str,
    experiment_id: str,
    metrics_by_dim: dict[str, dict[str, RegimeConditionedMetrics]],
) -> StrategyRegimeProfile:
    summaries: dict[str, RegimePerformanceSummary] = {
        dim: build_performance_summary(dim, metrics_by_label)
        for dim, metrics_by_label in metrics_by_dim.items()
    }

    all_sharpes: list[float] = [
        s.sensitivity.sharpe_std for s in summaries.values() if s.sensitivity.sharpe_std > 0.0
    ]
    overall_sensitivity = float(np.mean(all_sharpes)) if all_sharpes else 0.0

    all_robustness: list[float] = [
        s.sensitivity.regime_robustness
        for s in summaries.values()
        if s.sensitivity.best_regime is not None
    ]
    is_regime_robust = bool(all_robustness and min(all_robustness) >= 0.0)

    return StrategyRegimeProfile(
        strategy_id=strategy_id,
        run_id=run_id,
        experiment_id=experiment_id,
        by_trend=summaries.get("trend", build_performance_summary("trend", {})),
        by_volatility=summaries.get("volatility", build_performance_summary("volatility", {})),
        by_liquidity=summaries.get("liquidity", build_performance_summary("liquidity", {})),
        by_mean_reversion=summaries.get(
            "mean_reversion", build_performance_summary("mean_reversion", {})
        ),
        by_risk=summaries.get("risk", build_performance_summary("risk", {})),
        overall_sensitivity=overall_sensitivity,
        is_regime_robust=is_regime_robust,
    )
