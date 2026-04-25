from __future__ import annotations


def simple_moving_average(values: list[float], window: int) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")

    if len(values) < window:
        return None

    subset = values[-window:]
    return sum(subset) / window


def exponential_moving_average(values: list[float], window: int) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")

    if len(values) < window:
        return None

    multiplier = 2 / (window + 1)
    ema = sum(values[:window]) / window

    for value in values[window:]:
        ema = (value - ema) * multiplier + ema

    return ema
