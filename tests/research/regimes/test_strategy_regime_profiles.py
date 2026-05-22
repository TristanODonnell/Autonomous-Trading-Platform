from __future__ import annotations

import pytest

from autonomous_trading_platform.research.analysis.regimes.regime_bucket import RegimeBucket
from autonomous_trading_platform.research.analysis.regimes.regime_metrics import (
    RegimeConditionedMetrics,
)
from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
    build_sensitivity_score,
    build_strategy_regime_profile,
)


def _metrics(
    dimension: str,
    label: str,
    sharpe: float | None,
    bar_count: int = 20,
) -> RegimeConditionedMetrics:
    return RegimeConditionedMetrics(
        bucket=RegimeBucket(dimension=dimension, label=label),
        bar_count=bar_count,
        exposure_fraction=bar_count / 100.0,
        total_return=None,
        cagr=None,
        sharpe=sharpe,
        sortino=None,
        volatility=None,
        max_drawdown=None,
        trade_count=0,
        win_rate=None,
        expectancy=None,
        profit_factor=None,
        avg_win=None,
        avg_loss=None,
        trade_frequency=None,
        avg_bar_return=None,
    )


class TestBuildSensitivityScore:
    def test_no_data_returns_zero_sensitivity(self) -> None:
        score = build_sensitivity_score("trend", {})
        assert score.sharpe_std == 0.0
        assert score.best_regime is None
        assert score.worst_regime is None

    def test_identifies_best_and_worst_regime(self) -> None:
        metrics = {
            "bull": _metrics("trend", "bull", 2.0),
            "bear": _metrics("trend", "bear", -1.0),
            "sideways": _metrics("trend", "sideways", 0.5),
        }
        score = build_sensitivity_score("trend", metrics)
        assert score.best_regime == "bull"
        assert score.worst_regime == "bear"

    def test_sharpe_range_correct(self) -> None:
        metrics = {
            "bull": _metrics("trend", "bull", 3.0),
            "bear": _metrics("trend", "bear", -1.0),
            "sideways": _metrics("trend", "sideways", 1.0),
        }
        score = build_sensitivity_score("trend", metrics)
        assert score.sharpe_range == pytest.approx(4.0)

    def test_robustness_is_min_sharpe(self) -> None:
        metrics = {
            "bull": _metrics("trend", "bull", 2.0),
            "bear": _metrics("trend", "bear", -0.5),
            "sideways": _metrics("trend", "sideways", 1.0),
        }
        score = build_sensitivity_score("trend", metrics)
        assert score.regime_robustness == pytest.approx(-0.5)

    def test_insufficient_bars_excluded(self) -> None:
        metrics = {
            "bull": _metrics("trend", "bull", 3.0, bar_count=20),
            "bear": _metrics("trend", "bear", -5.0, bar_count=5),  # below threshold
        }
        score = build_sensitivity_score("trend", metrics)
        # bear has bar_count=5 which is below _MIN_BARS_FOR_SENSITIVITY=10
        assert score.best_regime == "bull"
        assert score.worst_regime == "bull"

    def test_single_label_with_data(self) -> None:
        metrics = {
            "bull": _metrics("trend", "bull", 1.5),
            "bear": _metrics("trend", "bear", None),
            "sideways": _metrics("trend", "sideways", None),
        }
        score = build_sensitivity_score("trend", metrics)
        assert score.best_regime == "bull"
        assert score.worst_regime == "bull"
        assert score.sharpe_std == 0.0


class TestBuildStrategyRegimeProfile:
    def _make_metrics_by_dim(self) -> dict[str, dict[str, RegimeConditionedMetrics]]:
        return {
            "trend": {
                "bull": _metrics("trend", "bull", 2.0),
                "bear": _metrics("trend", "bear", -1.0),
                "sideways": _metrics("trend", "sideways", 0.5),
            },
            "volatility": {
                "high_volatility": _metrics("volatility", "high_volatility", -0.5),
                "normal_volatility": _metrics("volatility", "normal_volatility", 1.5),
                "low_volatility": _metrics("volatility", "low_volatility", 0.8),
            },
            "liquidity": {
                "high_liquidity": _metrics("liquidity", "high_liquidity", 1.0),
                "normal_liquidity": _metrics("liquidity", "normal_liquidity", 0.5),
                "low_liquidity": _metrics("liquidity", "low_liquidity", -0.2),
            },
            "mean_reversion": {
                "trending": _metrics("mean_reversion", "trending", 1.2),
                "mean_reverting": _metrics("mean_reversion", "mean_reverting", 0.3),
                "undefined": _metrics("mean_reversion", "undefined", None, bar_count=0),
            },
            "risk": {
                "risk_on": _metrics("risk", "risk_on", 1.8),
                "risk_off": _metrics("risk", "risk_off", -0.8),
                "neutral": _metrics("risk", "neutral", 0.6),
            },
        }

    def test_profile_has_all_dimensions(self) -> None:
        profile = build_strategy_regime_profile(
            strategy_id="momentum",
            run_id="run-001",
            experiment_id="exp-001",
            metrics_by_dim=self._make_metrics_by_dim(),
        )
        assert profile.strategy_id == "momentum"
        assert profile.by_trend is not None
        assert profile.by_volatility is not None
        assert profile.by_liquidity is not None
        assert profile.by_mean_reversion is not None
        assert profile.by_risk is not None

    def test_regime_robust_false_when_negative_sharpe_exists(self) -> None:
        profile = build_strategy_regime_profile(
            strategy_id="momentum",
            run_id="run-001",
            experiment_id="exp-001",
            metrics_by_dim=self._make_metrics_by_dim(),
        )
        # bear (-1.0) and risk_off (-0.8) have negative Sharpe
        assert profile.is_regime_robust is False

    def test_regime_robust_true_all_positive(self) -> None:
        all_positive = {
            dim: {label: _metrics(dim, label, 0.5) for label in labels}
            for dim, labels in {
                "trend": ["bull", "bear", "sideways"],
                "volatility": ["high_volatility", "normal_volatility", "low_volatility"],
                "liquidity": ["high_liquidity", "normal_liquidity", "low_liquidity"],
                "mean_reversion": ["trending", "mean_reverting", "undefined"],
                "risk": ["risk_on", "risk_off", "neutral"],
            }.items()
        }
        profile = build_strategy_regime_profile(
            strategy_id="momentum",
            run_id="run-001",
            experiment_id="exp-001",
            metrics_by_dim=all_positive,
        )
        assert profile.is_regime_robust is True

    def test_overall_sensitivity_is_non_negative(self) -> None:
        profile = build_strategy_regime_profile(
            strategy_id="momentum",
            run_id="run-001",
            experiment_id="exp-001",
            metrics_by_dim=self._make_metrics_by_dim(),
        )
        assert profile.overall_sensitivity >= 0.0

    def test_empty_metrics_by_dim_produces_defaults(self) -> None:
        profile = build_strategy_regime_profile(
            strategy_id="s",
            run_id="r",
            experiment_id="e",
            metrics_by_dim={},
        )
        assert profile.by_trend.sensitivity.best_regime is None
        assert profile.overall_sensitivity == 0.0
