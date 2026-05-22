from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.factor_based_strategy import (
    FactorBasedStrategy,
)
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000050")


def _make_bars(closes: list[float], volumes: list[float] | None = None):
    if volumes is None:
        volumes = [1000.0] * len(closes)
    return [
        make_five_minute_bar(
            timestamp=_BASE_TS + timedelta(minutes=5 * i),
            close_price=str(c),
            open_price=str(c),
            high_price=str(c),
            low_price=str(c),
            volume=int(v),
        )
        for i, (c, v) in enumerate(zip(closes, volumes, strict=True))
    ]


def _ctx(bars) -> StrategyContext:
    ts = _BASE_TS + timedelta(minutes=5 * (len(bars) + 1))
    return StrategyContext(
        run_id=_RUN_ID,
        strategy_id="fb_test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


class TestFactorBasedStrategySignals:
    def _make_strategy(
        self,
        *,
        strategy_id: str = "fb",
        momentum_lookback: int = 3,
        mean_reversion_window: int = 10,
        volatility_window: int = 10,
        volume_window: int = 10,
        buy_score_threshold: float = 0.4,
        sell_score_threshold: float = -0.4,
    ) -> FactorBasedStrategy:
        return FactorBasedStrategy(
            strategy_id=strategy_id,
            momentum_lookback=momentum_lookback,
            mean_reversion_window=mean_reversion_window,
            volatility_window=volatility_window,
            volume_window=volume_window,
            buy_score_threshold=buy_score_threshold,
            sell_score_threshold=sell_score_threshold,
        )

    def test_insufficient_bars_returns_none(self) -> None:
        strategy = self._make_strategy()
        bars = _make_bars([100.0] * 5)
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_signal_direction_is_buy_or_sell_or_none(self) -> None:
        strategy = self._make_strategy()
        closes = [100.0 + i * 0.5 for i in range(15)]
        bars = _make_bars(closes)
        signal = strategy.evaluate_symbol(_ctx(bars))
        if signal is not None:
            assert signal.direction in (SignalDirection.BUY, SignalDirection.SELL)

    def test_params_contain_all_factors(self) -> None:
        strategy = self._make_strategy()
        closes = [100.0 + i * 0.5 for i in range(15)]
        bars = _make_bars(closes)
        signal = strategy.evaluate_symbol(_ctx(bars))
        if signal is not None:
            assert signal.params is not None
            assert "momentum_value" in signal.params
            assert "z_score" in signal.params
            assert "volatility" in signal.params
            assert "volume_ratio" in signal.params
            assert "weighted_score" in signal.params
            assert signal.params["strategy_type"] == "factor_based"

    def test_constant_prices_returns_none(self) -> None:
        # constant prices → std=0 → z_score=None → strategy returns None
        strategy = self._make_strategy()
        closes = [100.0] * 15
        bars = _make_bars(closes)
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_determinism(self) -> None:
        strategy = self._make_strategy()
        closes = [100.0 + i * 0.5 for i in range(15)]
        bars = _make_bars(closes)
        ctx = _ctx(bars)
        s1 = strategy.evaluate_symbol(ctx)
        s2 = strategy.evaluate_symbol(ctx)
        if s1 is not None and s2 is not None:
            assert s1.signal_id == s2.signal_id
        else:
            assert s1 is s2  # both None


class TestFactorBasedStrategyValidation:
    def test_zero_momentum_lookback_raises(self) -> None:
        with pytest.raises(ValueError):
            FactorBasedStrategy(strategy_id="fb", momentum_lookback=0)

    def test_zero_mean_reversion_window_raises(self) -> None:
        with pytest.raises(ValueError):
            FactorBasedStrategy(strategy_id="fb", mean_reversion_window=0)

    def test_zero_volatility_window_raises(self) -> None:
        with pytest.raises(ValueError):
            FactorBasedStrategy(strategy_id="fb", volatility_window=0)

    def test_zero_volume_window_raises(self) -> None:
        with pytest.raises(ValueError):
            FactorBasedStrategy(strategy_id="fb", volume_window=0)

    def test_sell_threshold_must_be_less_than_buy_threshold(self) -> None:
        with pytest.raises(ValueError):
            FactorBasedStrategy(strategy_id="fb", buy_score_threshold=0.3, sell_score_threshold=0.5)

    def test_equal_thresholds_raises(self) -> None:
        with pytest.raises(ValueError):
            FactorBasedStrategy(strategy_id="fb", buy_score_threshold=0.5, sell_score_threshold=0.5)


class TestFactorBasedStrategyScoringLogic:
    def test_normalize_signed_positive_returns_one(self) -> None:
        assert FactorBasedStrategy._normalize_signed(0.1) == 1.0
        assert FactorBasedStrategy._normalize_signed(100.0) == 1.0

    def test_normalize_signed_negative_returns_minus_one(self) -> None:
        assert FactorBasedStrategy._normalize_signed(-0.1) == -1.0

    def test_normalize_signed_zero_returns_zero(self) -> None:
        assert FactorBasedStrategy._normalize_signed(0.0) == 0.0

    def test_score_mean_reversion_low_z_returns_one(self) -> None:
        assert FactorBasedStrategy._score_mean_reversion(-2.0) == 1.0
        assert FactorBasedStrategy._score_mean_reversion(-3.0) == 1.0

    def test_score_mean_reversion_high_z_returns_minus_one(self) -> None:
        assert FactorBasedStrategy._score_mean_reversion(2.0) == -1.0

    def test_score_mean_reversion_neutral_returns_zero(self) -> None:
        assert FactorBasedStrategy._score_mean_reversion(0.0) == 0.0
        assert FactorBasedStrategy._score_mean_reversion(1.5) == 0.0

    def test_score_volume_high_returns_one(self) -> None:
        assert FactorBasedStrategy._score_volume(1.5) == 1.0
        assert FactorBasedStrategy._score_volume(2.0) == 1.0

    def test_score_volume_low_returns_negative(self) -> None:
        assert FactorBasedStrategy._score_volume(0.5) == -0.5

    def test_score_volatility_nonzero_returns_negative(self) -> None:
        # Any positive volatility gets a -0.25 penalty
        assert FactorBasedStrategy._score_volatility(1.0) == -0.25
        assert FactorBasedStrategy._score_volatility(0.01) == -0.25

    def test_score_volatility_zero_returns_zero(self) -> None:
        assert FactorBasedStrategy._score_volatility(0.0) == 0.0
