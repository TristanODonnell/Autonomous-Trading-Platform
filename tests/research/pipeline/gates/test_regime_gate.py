"""Unit tests for RegimeGate pass/fail rules — synthetic regime profiles."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.analysis.regimes.regime_bucket import (
    REGIME_DIMENSIONS,
    REGIME_LABELS,
    RegimeBucket,
)
from autonomous_trading_platform.research.analysis.regimes.regime_metrics import (
    RegimeConditionedMetrics,
)
from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
    StrategyRegimeProfile,
    build_strategy_regime_profile,
)
from autonomous_trading_platform.research.pipeline.gates.regime_gate import (
    RegimeGateConfig,
    evaluate_regime_gate,
)


def _m(
    dimension: str,
    label: str,
    *,
    sharpe: float | None,
    bars: int = 50,
    total_return: float | None = None,
    max_drawdown: float | None = -0.05,
) -> RegimeConditionedMetrics:
    if total_return is None and sharpe is not None:
        total_return = 0.01 if sharpe > 0 else -0.01
    return RegimeConditionedMetrics(
        bucket=RegimeBucket(dimension=dimension, label=label),
        bar_count=bars,
        exposure_fraction=0.5,
        total_return=total_return,
        cagr=None,
        sharpe=sharpe,
        sortino=None,
        volatility=None,
        max_drawdown=max_drawdown,
        trade_count=0,
        win_rate=None,
        expectancy=None,
        profit_factor=None,
        avg_win=None,
        avg_loss=None,
        trade_frequency=None,
        avg_bar_return=None,
    )


def _profile(**by_dim: dict[str, RegimeConditionedMetrics]) -> StrategyRegimeProfile:
    metrics_by_dim = {dim: by_dim.get(dim, {}) for dim in REGIME_DIMENSIONS}
    return build_strategy_regime_profile(
        strategy_id="s1", run_id="r1", experiment_id="e1", metrics_by_dim=metrics_by_dim
    )


def _trend(**sharpes: float) -> dict[str, RegimeConditionedMetrics]:
    return {label: _m("trend", label, sharpe=s) for label, s in sharpes.items()}


CFG = RegimeGateConfig(dimensions=("trend",), min_bars_per_regime=20)


class TestRegimeGate:
    def test_passes_when_profitable_across_regimes(self) -> None:
        verdict = evaluate_regime_gate(
            strategy_id="s1", profile=_profile(trend=_trend(bull=1.5, bear=0.4)), config=CFG
        )
        assert verdict.passed
        assert verdict.evaluated_dimensions == ["trend"]

    def test_fails_on_catastrophic_regime(self) -> None:
        verdict = evaluate_regime_gate(
            strategy_id="s1",
            profile=_profile(trend=_trend(bull=3.0, bear=-2.0, sideways=0.5)),
            config=CFG,
        )
        assert not verdict.passed
        assert any("trend:bear Sharpe -2.00" in r for r in verdict.failure_reasons)

    def test_fails_when_profitable_in_too_few_regimes(self) -> None:
        # Worst Sharpe (-0.3) is above the floor, but only 1/3 regimes is profitable.
        verdict = evaluate_regime_gate(
            strategy_id="s1",
            profile=_profile(trend=_trend(bull=2.0, bear=-0.3, sideways=-0.2)),
            config=CFG,
        )
        assert not verdict.passed
        assert any("profitable in 1/3" in r for r in verdict.failure_reasons)

    def test_fails_on_regime_drawdown(self) -> None:
        trend = {
            "bull": _m("trend", "bull", sharpe=1.0),
            "bear": _m("trend", "bear", sharpe=0.2, max_drawdown=-0.40),
        }
        verdict = evaluate_regime_gate(strategy_id="s1", profile=_profile(trend=trend), config=CFG)
        assert not verdict.passed
        assert any("drawdown" in r for r in verdict.failure_reasons)

    def test_thin_buckets_are_not_evaluated(self) -> None:
        # The losing bear bucket has only 5 bars → ignored → only bull evaluable
        # → dimension cannot be judged (needs 2 regimes).
        trend = {
            "bull": _m("trend", "bull", sharpe=1.0),
            "bear": _m("trend", "bear", sharpe=-5.0, bars=5),
        }
        verdict = evaluate_regime_gate(strategy_id="s1", profile=_profile(trend=trend), config=CFG)
        assert verdict.passed  # default policy: insufficient coverage passes
        assert verdict.evaluated_dimensions == []
        assert any("insufficient regime coverage" in w for w in verdict.warnings)

    def test_insufficient_coverage_can_fail(self) -> None:
        cfg = RegimeGateConfig(dimensions=("trend",), on_insufficient_coverage="fail")
        verdict = evaluate_regime_gate(
            strategy_id="s1", profile=_profile(trend=_trend(bull=1.0)), config=cfg
        )
        assert not verdict.passed
        assert "insufficient regime coverage" in verdict.failure_reasons[0]

    def test_missing_profile_uses_policy_and_records_reason(self) -> None:
        verdict = evaluate_regime_gate(
            strategy_id="s1", profile=None, config=CFG, unavailable_reason="no bars"
        )
        assert verdict.passed
        assert "no bars" in verdict.warnings[0]

    def test_unevaluated_dimension_does_not_block_evaluated_one(self) -> None:
        cfg = RegimeGateConfig(dimensions=("trend", "volatility"), min_bars_per_regime=20)
        verdict = evaluate_regime_gate(
            strategy_id="s1",
            profile=_profile(trend=_trend(bull=1.0, bear=-3.0)),  # volatility empty
            config=cfg,
        )
        assert not verdict.passed
        assert verdict.evaluated_dimensions == ["trend"]
        assert any("volatility: not evaluated" in w for w in verdict.warnings)

    def test_every_label_in_catalogue_is_accepted(self) -> None:
        for dim, labels in REGIME_LABELS.items():
            metrics = {label: _m(dim, label, sharpe=1.0) for label in labels}
            verdict = evaluate_regime_gate(
                strategy_id="s1",
                profile=_profile(**{dim: metrics}),
                config=RegimeGateConfig(dimensions=(dim,)),
            )
            assert verdict.passed

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"dimensions": ()}, "dimensions"),
            ({"dimensions": ("weather",)}, "Unknown regime dimensions"),
            ({"min_bars_per_regime": 1}, "min_bars_per_regime"),
            ({"max_regime_drawdown": 0.1}, "max_regime_drawdown"),
            ({"min_positive_regime_fraction": 1.5}, "min_positive_regime_fraction"),
            ({"on_insufficient_coverage": "maybe"}, "on_insufficient_coverage"),
        ],
    )
    def test_config_validation(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            RegimeGateConfig(**kwargs)
