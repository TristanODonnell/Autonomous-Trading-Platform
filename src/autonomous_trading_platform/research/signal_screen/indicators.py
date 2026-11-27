"""The 13 indicators of ``strategy/indicators/``, computed for every bar at once.

A strategy asks an indicator for one number, from the bars it is handed on that bar.
The screen needs that number on every bar of the history, so each function here returns
the whole column: entry ``t`` is what the strategy function returns when the last bar it
is handed is bar ``t``. A parity test holds the two equal
(``tests/research/signal_screen/test_indicator_parity.py``), so the screen tests the
number that is traded.

Where a strategy function returns ``None`` (too few bars, a zero divisor) the column
holds NaN, and the screen leaves that bar out.

**Split terms.** The context builder hands a strategy its bars in the share terms of
the bar it trades on (``adjust_bars_for_splits``, ``as_of`` the evaluation day): bars
before a split are restated. An indicator in dollars (a moving average, a price
change) or in shares (an average volume) is therefore in the terms of the bar after
the one it read; a ratio (z-score, rate of change, RSI, volume ratio) is the same in
any terms. ``IndicatorInput.terms`` carries the restatement.

**Two indicators depend on how many bars they are handed**, not only on a window:
``exponential_moving_average`` is seeded from the first ``window`` bars it is given and
smoothed over the rest, and ``rsi_wilder`` likewise. A strategy is handed its warmup
count (a composite: its longest warmup), so these take ``context_bars`` and are NaN
until that many bars exist.

**Flat windows.** ``z_score`` returns ``None`` when the window's standard deviation is
exactly zero. When every close in the window is equal the deviation is often a rounding
residue instead (1e-17), and the strategy function then divides one residue by another.
The column is NaN on any window whose deviation is within ``FLAT_WINDOW_RTOL`` of zero
relative to its mean: the one place it departs from the strategy function, on bars
where that function's value is noise. ``flat_windows`` counts them.

Volumes: the context builder rounds a restated volume to a whole share; the columns do
not, a difference below one share on bars before a split.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# A window's deviation at or below this share of its mean is rounding, not movement.
FLAT_WINDOW_RTOL = 1e-12

Column: TypeAlias = np.ndarray


@dataclass(frozen=True)
class IndicatorInput:
    """One name's bars in time order.

    close, volume : as traded on the day (raw).
    terms         : the price factor that restates each bar in the share terms of the
                    last bar: the product of 1 / ratio over the splits after it (1.0
                    where none follows).
    traded_terms  : the terms of the bar each row's decision trades on, when that is
                    not the next row (the daily scale trades on a bar of the same day).
    """

    close: Column
    volume: Column
    terms: Column
    traded_terms: Column | None = None

    def __post_init__(self) -> None:
        if not len(self.close) == len(self.volume) == len(self.terms):
            raise ValueError("close, volume and terms must be the same length")
        if self.traded_terms is not None and len(self.traded_terms) != len(self.close):
            raise ValueError("traded_terms must be as long as close")

    @classmethod
    def without_splits(cls, close: Column, volume: Column | None = None) -> IndicatorInput:
        close = np.asarray(close, dtype=float)
        volume = np.zeros(len(close)) if volume is None else np.asarray(volume, dtype=float)
        return cls(close=close, volume=volume, terms=np.ones(len(close)))

    @property
    def restated_close(self) -> Column:
        """Closes in the terms of the last bar: a continuous series across splits."""
        return self.close * self.terms

    @property
    def restated_volume(self) -> Column:
        return self.volume / self.terms

    @property
    def decision_terms(self) -> Column:
        """The terms of the bar each decision trades on: ``traded_terms`` when given,
        else the next bar's (the last bar's own, having no next)."""
        if self.traded_terms is not None:
            return self.traded_terms
        if len(self.terms) == 0:
            return self.terms
        return np.append(self.terms[1:], self.terms[-1])


# --------------------------------------------------------------------- building blocks


def _require_positive(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _nan(n: int) -> Column:
    return np.full(n, np.nan)


def _placed(n: int, first: int, values: Column) -> Column:
    """``values`` written from index ``first`` on; NaN before it."""
    out = _nan(n)
    out[first:] = values
    return out


def _rolling_mean(x: Column, window: int) -> Column:
    if len(x) < window:
        return _nan(len(x))
    return _placed(len(x), window - 1, sliding_window_view(x, window).mean(axis=1))


def _rolling_std(x: Column, window: int) -> Column:
    """Sample standard deviation (n - 1) of the trailing window; 0.0 for a window of one."""
    if len(x) < window:
        return _nan(len(x))
    if window == 1:
        return np.zeros(len(x))
    windows = sliding_window_view(x, window)
    mean = windows.mean(axis=1)
    variance = ((windows - mean[:, None]) ** 2).sum(axis=1) / (window - 1)
    return _placed(len(x), window - 1, np.sqrt(variance))


def _divide(numerator: Column, denominator: Column) -> Column:
    """numerator / denominator, NaN where the denominator is zero or either is NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = numerator / denominator
    out[~np.isfinite(out)] = np.nan
    return out


def _flat(mean: Column, std: Column) -> Column:
    with np.errstate(invalid="ignore"):
        return std <= FLAT_WINDOW_RTOL * np.abs(mean)


# --------------------------------------------------------------------- trend


def simple_moving_average(data: IndicatorInput, *, window: int) -> Column:
    _require_positive(window, "window")
    return _rolling_mean(data.restated_close, window) / data.decision_terms


def exponential_moving_average(
    data: IndicatorInput, *, window: int, context_bars: int | None = None
) -> Column:
    """Seeded with the mean of the first ``window`` of the ``context_bars`` bars handed
    over, then smoothed over the rest. ``context_bars`` defaults to ``window`` (the
    indicator's own warmup), where it equals the simple average."""
    _require_positive(window, "window")
    context = window if context_bars is None else context_bars
    if context < window:
        raise ValueError("context_bars must be at least window")
    closes = data.restated_close
    if len(closes) < context:
        return _nan(len(closes))
    windows = sliding_window_view(closes, context)
    ema = windows[:, :window].mean(axis=1)
    multiplier = 2 / (window + 1)
    for k in range(window, context):
        ema = (windows[:, k] - ema) * multiplier + ema
    return _placed(len(closes), context - 1, ema) / data.decision_terms


# --------------------------------------------------------------------- mean reversion


def distance_from_moving_average(data: IndicatorInput, *, window: int) -> Column:
    _require_positive(window, "window")
    closes = data.restated_close
    return (closes - _rolling_mean(closes, window)) / data.decision_terms


def z_score(data: IndicatorInput, *, window: int) -> Column:
    _require_positive(window, "window")
    closes = data.restated_close
    mean = _rolling_mean(closes, window)
    std = _rolling_std(closes, window)
    out = _divide(closes - mean, std)
    out[_flat(mean, std)] = np.nan
    return out


def flat_windows(data: IndicatorInput, *, window: int) -> Column:
    """True on the bars whose trailing window did not move (see the module docstring)."""
    closes = data.restated_close
    mean = _rolling_mean(closes, window)
    return _flat(mean, _rolling_std(closes, window)) & ~np.isnan(mean)


# --------------------------------------------------------------------- momentum


def momentum(data: IndicatorInput, *, lookback: int = 1) -> Column:
    _require_positive(lookback, "lookback")
    closes = data.restated_close
    if len(closes) <= lookback:
        return _nan(len(closes))
    change = _placed(len(closes), lookback, closes[lookback:] - closes[:-lookback])
    return change / data.decision_terms


def rate_of_change(data: IndicatorInput, *, lookback: int = 1) -> Column:
    _require_positive(lookback, "lookback")
    closes = data.restated_close
    if len(closes) <= lookback:
        return _nan(len(closes))
    previous = closes[:-lookback]
    return _placed(len(closes), lookback, _divide(closes[lookback:] - previous, previous))


def _rsi_from(avg_gain: Column, avg_loss: Column) -> Column:
    with np.errstate(divide="ignore", invalid="ignore"):
        value = 100 - 100 / (1 + avg_gain / avg_loss)
    return np.where(avg_loss == 0, 100.0, value)


def rsi(data: IndicatorInput, *, window: int = 14) -> Column:
    """Cutler's RSI: plain averages of the last ``window`` gains and losses."""
    _require_positive(window, "window")
    closes = data.restated_close
    if len(closes) < window + 1:
        return _nan(len(closes))
    deltas = sliding_window_view(np.diff(closes), window)
    avg_gain = np.where(deltas >= 0, deltas, 0.0).sum(axis=1) / window
    avg_loss = np.where(deltas < 0, -deltas, 0.0).sum(axis=1) / window
    return _placed(len(closes), window, _rsi_from(avg_gain, avg_loss))


def rsi_wilder(
    data: IndicatorInput, *, window: int = 14, context_bars: int | None = None
) -> Column:
    """Wilder's RSI over the ``context_bars`` bars handed over: seeded on the first
    ``window`` changes, smoothed over the rest. Defaults to ``2 * window + 1`` bars,
    the fewest the strategy function accepts."""
    _require_positive(window, "window")
    context = 2 * window + 1 if context_bars is None else context_bars
    if context < 2 * window + 1:
        raise ValueError("context_bars must be at least 2 * window + 1")
    closes = data.restated_close
    if len(closes) < context:
        return _nan(len(closes))
    deltas = np.diff(sliding_window_view(closes, context), axis=1)
    seed = deltas[:, :window]
    avg_gain = np.where(seed > 0, seed, 0.0).sum(axis=1) / window
    avg_loss = np.where(seed < 0, -seed, 0.0).sum(axis=1) / window
    alpha = 1.0 / window
    for k in range(window, context - 1):
        delta = deltas[:, k]
        avg_gain = alpha * np.where(delta > 0, delta, 0.0) + (1 - alpha) * avg_gain
        avg_loss = alpha * np.where(delta < 0, -delta, 0.0) + (1 - alpha) * avg_loss
    return _placed(len(closes), context - 1, _rsi_from(avg_gain, avg_loss))


# --------------------------------------------------------------------- volatility


def rolling_standard_deviation(data: IndicatorInput, *, window: int) -> Column:
    """Of the closes, in dollars."""
    _require_positive(window, "window")
    return _rolling_std(data.restated_close, window) / data.decision_terms


def realized_volatility(data: IndicatorInput, *, window: int) -> Column:
    """Of the bar-to-bar returns (what a composite hands the strategy function)."""
    _require_positive(window, "window")
    closes = data.restated_close
    if len(closes) < 2:
        return _nan(len(closes))
    returns = _divide(np.diff(closes), closes[:-1])
    return _placed(len(closes), 1, _rolling_std(returns, window))


# --------------------------------------------------------------------- volume


def average_volume(data: IndicatorInput, *, window: int) -> Column:
    _require_positive(window, "window")
    return _rolling_mean(data.restated_volume, window) * data.decision_terms


def volume_ratio(data: IndicatorInput, *, window: int) -> Column:
    _require_positive(window, "window")
    volumes = data.restated_volume
    return _divide(volumes, _rolling_mean(volumes, window))


def volume_spike(data: IndicatorInput, *, window: int, threshold: float = 2.0) -> Column:
    """1.0 where the volume ratio is at or above ``threshold``, 0.0 where it is below,
    NaN where there is no ratio (the strategy function reads False there)."""
    ratio = volume_ratio(data, window=window)
    return np.where(np.isnan(ratio), np.nan, (ratio >= threshold).astype(float))


# --------------------------------------------------------------------- registry

INDICATORS: dict[str, Callable[..., Column]] = {
    "simple_moving_average": simple_moving_average,
    "exponential_moving_average": exponential_moving_average,
    "distance_from_moving_average": distance_from_moving_average,
    "z_score": z_score,
    "momentum": momentum,
    "rate_of_change": rate_of_change,
    "rsi": rsi,
    "rsi_wilder": rsi_wilder,
    "rolling_standard_deviation": rolling_standard_deviation,
    "realized_volatility": realized_volatility,
    "average_volume": average_volume,
    "volume_ratio": volume_ratio,
    "volume_spike": volume_spike,
}


def indicator_column(name: str, data: IndicatorInput, **parameters: float) -> Column:
    """The named indicator on every bar of ``data``."""
    try:
        function = INDICATORS[name]
    except KeyError:
        raise ValueError(
            f"unknown indicator {name!r}; the screen knows {sorted(INDICATORS)}"
        ) from None
    return function(data, **parameters)
