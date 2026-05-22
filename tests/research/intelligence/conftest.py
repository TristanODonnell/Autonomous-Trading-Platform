"""Shared fixtures for research intelligence tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    ResearchFeatureVectorBuilder,
)

# ---------------------------------------------------------------------------
# Minimal ValidationSummary-like fixture
# ---------------------------------------------------------------------------


def _make_stage(summary: dict) -> SimpleNamespace:
    return SimpleNamespace(summary=summary)


def make_validation_summary(
    *,
    overall: float = 0.70,
    walk_forward_consistency: float | None = 0.75,
    mc_stability: float | None = 0.80,
    regime_robustness: float | None = 0.65,
    parameter_stability: float | None = 0.70,
    stress_resilience: float | None = 0.71,
    overfitting_resistance: float | None = 0.80,
    overfit_probability: float = 0.20,
    fold_consistency: float = 0.80,
    avg_test_sharpe: float = 0.90,
    train_test_degradation: float = 0.10,
    fold_sharpe_cv: float = 0.30,
    survival_rate: float = 0.86,
    avg_sharpe_degradation: float = 0.15,
    low_trade_count_flag: bool = False,
    # Per-indicator overrides for overfitting signals
    ind_train_test_degradation: float = 0.05,
    ind_fold_instability: float = 0.10,
    ind_mc_instability: float = 0.08,
    ind_regime_concentration: float = 0.70,
    ind_narrow_period_alpha: float = 0.30,
    ind_parameter_fragility: float = 0.15,
) -> Any:
    rs = SimpleNamespace(
        overall=overall,
        walk_forward_consistency=walk_forward_consistency,
        mc_stability=mc_stability,
        regime_robustness=regime_robustness,
        parameter_stability=parameter_stability,
        stress_resilience=stress_resilience,
        overfitting_resistance=overfitting_resistance,
    )
    stage_results = {
        "overfitting": _make_stage(
            {
                "overfitting_probability": overfit_probability,
                "resistance_score": 1.0 - overfit_probability,
                "indicators": {
                    "train_test_degradation": ind_train_test_degradation,
                    "fold_instability": ind_fold_instability,
                    "mc_instability": ind_mc_instability,
                    "regime_concentration": ind_regime_concentration,
                    "low_trade_count_flag": low_trade_count_flag,
                    "narrow_period_alpha": ind_narrow_period_alpha,
                    "parameter_fragility": ind_parameter_fragility,
                },
            }
        ),
        "walk_forward": _make_stage(
            {
                "fold_consistency": fold_consistency,
                "avg_test_sharpe": avg_test_sharpe,
                "train_test_degradation": train_test_degradation,
                "fold_sharpe_cv": fold_sharpe_cv,
            }
        ),
        "stress_test": _make_stage(
            {
                "survival_rate": survival_rate,
                "avg_sharpe_degradation": avg_sharpe_degradation,
            }
        ),
    }
    return SimpleNamespace(
        robustness_score=rs,
        stage_results=stage_results,
        overall_passed=overall >= 0.5,
        validation_run_id="test-run-001",
    )


def make_poor_validation_summary() -> Any:
    return make_validation_summary(
        overall=0.25,
        walk_forward_consistency=0.20,
        mc_stability=0.30,
        regime_robustness=0.15,
        parameter_stability=0.20,
        stress_resilience=0.30,
        overfitting_resistance=0.25,
        overfit_probability=0.75,
        fold_consistency=0.20,
        avg_test_sharpe=-0.30,
        train_test_degradation=0.80,
        fold_sharpe_cv=1.20,
        survival_rate=0.14,
        avg_sharpe_degradation=0.85,
        low_trade_count_flag=True,
        # Bad indicator values
        ind_train_test_degradation=0.75,
        ind_fold_instability=0.70,
        ind_mc_instability=0.65,
        ind_regime_concentration=0.25,  # low = concentrated in few regimes
        ind_narrow_period_alpha=0.80,
        ind_parameter_fragility=0.70,
    )


# ---------------------------------------------------------------------------
# Minimal StrategyRegimeProfile-like fixture
# ---------------------------------------------------------------------------


def _make_metrics(sharpe: float) -> SimpleNamespace:
    return SimpleNamespace(
        sharpe=sharpe,
        bar_count=100,
        trade_count=20,
    )


def _make_dim_summary(sharpe_values: dict[str, float]) -> SimpleNamespace:
    metrics_by_label = {label: _make_metrics(s) for label, s in sharpe_values.items()}
    vals = list(sharpe_values.values())
    import numpy as np

    std = float(np.std(vals, ddof=0)) if len(vals) > 1 else 0.0
    best = max(sharpe_values, key=lambda k: sharpe_values[k])
    worst = min(sharpe_values, key=lambda k: sharpe_values[k])
    sensitivity = SimpleNamespace(
        sharpe_std=std,
        sharpe_range=max(vals) - min(vals),
        best_regime=best,
        worst_regime=worst,
        regime_robustness=min(vals),
    )
    return SimpleNamespace(metrics_by_label=metrics_by_label, sensitivity=sensitivity)


def make_regime_profile(
    *,
    trend: dict | None = None,
    volatility: dict | None = None,
    is_regime_robust: bool = True,
    overall_sensitivity: float = 0.30,
) -> Any:
    trend = trend or {"bull": 1.2, "bear": 0.3, "sideways": 0.5}
    volatility = volatility or {
        "high_volatility": 0.4,
        "normal_volatility": 0.9,
        "low_volatility": 0.7,
    }
    liquidity = {"high_liquidity": 0.8, "normal_liquidity": 0.6, "low_liquidity": 0.5}
    mean_rev = {"trending": 0.3, "mean_reverting": 1.1, "undefined": 0.5}
    risk = {"risk_on": 1.0, "risk_off": 0.2, "neutral": 0.6}

    return SimpleNamespace(
        strategy_id="test_strat",
        run_id="test_run",
        experiment_id="test_exp",
        is_regime_robust=is_regime_robust,
        overall_sensitivity=overall_sensitivity,
        by_trend=_make_dim_summary(trend),
        by_volatility=_make_dim_summary(volatility),
        by_liquidity=_make_dim_summary(liquidity),
        by_mean_reversion=_make_dim_summary(mean_rev),
        by_risk=_make_dim_summary(risk),
    )


@pytest.fixture
def good_validation_summary():
    return make_validation_summary()


@pytest.fixture
def poor_validation_summary():
    return make_poor_validation_summary()


@pytest.fixture
def regime_profile():
    return make_regime_profile()


@pytest.fixture
def good_feature_vector(good_validation_summary, regime_profile):
    builder = ResearchFeatureVectorBuilder()
    return builder.build(
        strategy_id="good_strat",
        experiment_id="exp_001",
        dataset_version="v1",
        validation_summary=good_validation_summary,
        regime_profile=regime_profile,
        strategy_family="momentum",
        n_parameters=3,
    )


@pytest.fixture
def poor_feature_vector(poor_validation_summary):
    builder = ResearchFeatureVectorBuilder()
    return builder.build(
        strategy_id="poor_strat",
        experiment_id="exp_001",
        dataset_version="v1",
        validation_summary=poor_validation_summary,
        strategy_family="momentum",
        n_parameters=3,
    )
