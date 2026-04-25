from __future__ import annotations

from autonomous_trading_platform.strategy.indicators.trend import (
    simple_moving_average,
)
from autonomous_trading_platform.strategy.indicators.volatility import (
    rolling_standard_deviation,
)


def distance_from_moving_average(
    values: list[float],
    window: int,
) -> float | None:
    ma = simple_moving_average(values, window)

    if ma is None:
        return None

    return values[-1] - ma


def z_score(values: list[float], window: int) -> float | None:
    ma = simple_moving_average(values, window)
    std = rolling_standard_deviation(values, window)

    if ma is None or std is None or std == 0:
        return None

    return (values[-1] - ma) / std
