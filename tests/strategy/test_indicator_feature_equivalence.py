from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.feature_engineering.services.liquidity_feature_service import (
    LiquidityFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.moving_average_feature_service import (
    MovingAverageFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.returns_feature_service import (
    ReturnsFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.volatility_feature_service import (
    VolatilityFeatureService,
)
from autonomous_trading_platform.strategy.indicators.momentum import momentum, rate_of_change
from autonomous_trading_platform.strategy.indicators.trend import simple_moving_average
from autonomous_trading_platform.strategy.indicators.volatility import realized_volatility
from autonomous_trading_platform.strategy.indicators.volume import average_volume, volume_ratio


def _bars_frame() -> pd.DataFrame:
    timestamps = [datetime(2024, 1, 2, 14, 30, tzinfo=UTC) + timedelta(days=i) for i in range(10)]
    closes = [100.0, 101.0, 103.0, 102.0, 106.0, 109.0, 108.0, 111.0, 115.0, 117.0]
    volumes = [1000, 1100, 1200, 1300, 1400, 1600, 1500, 1700, 1800, 1900]
    return pd.DataFrame(
        {
            "symbol": ["AAPL"] * len(timestamps),
            "timestamp": timestamps,
            "close": closes,
            "volume": volumes,
        }
    )


def test_strategy_sma_matches_moving_average_feature_service_last_value() -> None:
    bars = _bars_frame()
    window = 5

    features = MovingAverageFeatureService().compute(
        bars,
        window=window,
        output_column="moving_average_value",
    )

    assert features["moving_average_value"].iloc[-1] == pytest.approx(
        simple_moving_average(bars["close"].tolist(), window)
    )


def test_strategy_rate_of_change_matches_returns_feature_service_for_same_lookback() -> None:
    bars = _bars_frame()
    lookback = 5

    features = ReturnsFeatureService().compute(
        bars,
        source_dataset_version_id="bars-v1",
        price_basis=PriceBasis.RAW,
    )

    assert features["ret_5d"].iloc[-1] == pytest.approx(
        rate_of_change(bars["close"].tolist(), lookback)
    )


def test_strategy_momentum_is_intentionally_absolute_not_feature_percent_return() -> None:
    bars = _bars_frame()
    lookback = 5

    features = ReturnsFeatureService().compute(
        bars,
        source_dataset_version_id="bars-v1",
        price_basis=PriceBasis.RAW,
    )

    assert momentum(bars["close"].tolist(), lookback) == pytest.approx(11.0)
    assert features["ret_5d"].iloc[-1] == pytest.approx(11.0 / 106.0)


def test_strategy_realized_volatility_matches_volatility_feature_service() -> None:
    bars = _bars_frame()
    returns = ReturnsFeatureService().compute(
        bars,
        source_dataset_version_id="bars-v1",
        price_basis=PriceBasis.RAW,
    )
    window = 5

    features = VolatilityFeatureService().compute(
        returns,
        returns_column="ret_1d",
        window=window,
        output_column="volatility_value",
    )

    assert features["volatility_value"].iloc[-1] == pytest.approx(
        realized_volatility(returns["ret_1d"].dropna().tolist(), window)
    )


def test_strategy_average_volume_matches_liquidity_feature_service() -> None:
    bars = _bars_frame()
    window = 5

    features = LiquidityFeatureService().compute(
        bars,
        avg_volume_window=window,
        avg_volume_output_column="avg_volume_value",
    )

    assert features["avg_volume_value"].iloc[-1] == pytest.approx(
        average_volume(bars["volume"].tolist(), window)
    )


def test_strategy_volume_ratio_is_derived_from_liquidity_average_volume() -> None:
    bars = _bars_frame()
    window = 5

    features = LiquidityFeatureService().compute(
        bars,
        avg_volume_window=window,
        avg_volume_output_column="avg_volume_value",
    )
    feature_ratio = bars["volume"].iloc[-1] / features["avg_volume_value"].iloc[-1]

    assert feature_ratio == pytest.approx(volume_ratio(bars["volume"].tolist(), window))
