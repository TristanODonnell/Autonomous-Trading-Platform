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
    """Compute RSI using Cutler's method (simple moving average of gains/losses).

    This is **Cutler's RSI**, not Wilder's original formulation.

    Cutler's RSI (this function)
    ----------------------------
    avg_gain / avg_loss are plain SMA over the ``window`` period.  The result
    is path-independent: you always get the same value given the same trailing
    ``window + 1`` prices, regardless of history length.  This makes it
    deterministic and easy to test.

    Wilder's RSI (see ``rsi_wilder`` below)
    ----------------------------------------
    avg_gain / avg_loss are seeded with the first-period SMA and then smoothed
    with Wilder's EMA (alpha = 1 / window) over all subsequent bars.  The
    result is path-dependent and converges only after ~3x the window in bars,
    which is why most charting platforms "warm up" for 3-5x the period before
    showing a value.

    Practical difference
    --------------------
    On a 14-period window the two formulas diverge by 1-3 RSI points in
    trending conditions.  Classic thresholds from the literature (overbought
    >= 70, oversold <= 30) are defined against **Wilder's** RSI.  If strategy
    thresholds were tuned against Cutler's output they are internally
    consistent; if they were imported from external literature they will
    trigger at the wrong times.

    Current status
    --------------
    No live strategy references ``rsi()`` thresholds tuned from external
    sources today, so this is safe for now.  If that changes, migrate callers
    to ``rsi_wilder()`` and re-tune thresholds.
    """
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


def rsi_wilder(values: list[float], window: int = 14) -> float | None:
    """Compute RSI using Wilder's original exponential-smoothing method.

    Requires at least ``2 * window + 1`` prices to produce a meaningful result:
    the first ``window`` deltas seed the initial SMA, and the remaining bars
    apply Wilder's EMA (alpha = 1 / window).  Returns ``None`` until the
    minimum history is available.

    This is the formulation used by most charting platforms and the basis for
    published threshold conventions (overbought >= 70, oversold <= 30).  Prefer
    this function when comparing RSI values against external literature or when
    thresholds were not derived from ``rsi()`` output directly.
    """
    if window <= 0:
        raise ValueError("window must be positive")

    # Need window+1 prices for the seed period plus at least window more bars
    # to smooth — require 2*window+1 so the EMA has had time to stabilise.
    if len(values) < 2 * window + 1:
        return None

    alpha = 1.0 / window

    # Seed: plain SMA over the first window of deltas.
    seed_deltas = [values[i] - values[i - 1] for i in range(1, window + 1)]
    avg_gain = sum(d for d in seed_deltas if d > 0) / window
    avg_loss = sum(abs(d) for d in seed_deltas if d < 0) / window

    # Smooth the remaining deltas with Wilder's EMA.
    for i in range(window + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = abs(delta) if delta < 0 else 0.0
        avg_gain = alpha * gain + (1 - alpha) * avg_gain
        avg_loss = alpha * loss + (1 - alpha) * avg_loss

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
