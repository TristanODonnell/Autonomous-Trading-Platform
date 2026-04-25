from __future__ import annotations

import math


def rolling_standard_deviation(
    values: list[float],
    window: int,
) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")

    if len(values) < window:
        return None

    subset = values[-window:]
    mean = sum(subset) / window

    variance = sum((value - mean) ** 2 for value in subset) / window

    return math.sqrt(variance)


def realized_volatility(
    returns: list[float],
    window: int,
) -> float | None:
    return rolling_standard_deviation(returns, window)
