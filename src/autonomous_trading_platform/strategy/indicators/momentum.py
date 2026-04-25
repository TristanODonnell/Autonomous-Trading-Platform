from __future__ import annotations


def momentum(values: list[float], lookback: int = 1) -> float | None:
    if lookback <= 0:
        raise ValueError("lookback must be positive")

    if len(values) <= lookback:
        return None

    return values[-1] - values[-1 - lookback]


def rate_of_change(values: list[float], lookback: int = 1) -> float | None:
    if lookback <= 0:
        raise ValueError("lookback must be positive")

    if len(values) <= lookback:
        return None

    previous = values[-1 - lookback]

    if previous == 0:
        return None

    return (values[-1] - previous) / previous


def rsi(values: list[float], window: int = 14) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")

    if len(values) < window + 1:
        return None

    gains = []
    losses = []

    for i in range(-window, 0):
        delta = values[i] - values[i - 1]

        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
