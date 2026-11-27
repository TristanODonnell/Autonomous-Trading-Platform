"""The screen's whole-column indicators equal the strategies' own functions.

For every bar of a made-up history, each column's entry is compared with what the
function in ``strategy/indicators/`` returns when handed the bars a strategy would be
handed on that bar: the trailing bars, restated in the share terms of the bar it trades
on. The history holds a 2-for-1 split and a stretch where the price does not move.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from autonomous_trading_platform.research.signal_screen import indicators as screen
from autonomous_trading_platform.strategy.indicators import (
    mean_reversion,
    momentum,
    trend,
    volatility,
    volume,
)

BARS = 260
SPLIT_AT = 150  # first bar in post-split terms
FLAT = range(60, 95)  # closes equal on these bars
CONTEXT = 120  # bars handed to a windowed indicator (more than any window below)


def _history() -> screen.IndicatorInput:
    rng = np.random.default_rng(7)
    steps = rng.normal(0.0, 0.35, BARS)
    for index in FLAT:
        steps[index] = 0.0
    restated = np.round(100.0 + np.cumsum(steps), 2)  # in post-split terms, to the cent
    terms = np.where(np.arange(BARS) < SPLIT_AT, 0.5, 1.0)
    volume_restated = rng.integers(1_000, 50_000, BARS).astype(float) * 2
    return screen.IndicatorInput(
        close=restated / terms,  # twice the price before the split
        volume=volume_restated * terms,  # half the shares before it
        terms=terms,
    )


def _handed(data: screen.IndicatorInput, t: int, bars: int) -> tuple[list[float], list[float]]:
    """The closes and volumes a strategy is handed when the last bar it reads is ``t``:
    restated in the terms of the bar it trades on (the next one)."""
    first = max(0, t - bars + 1)
    as_of = data.terms[min(t + 1, len(data.terms) - 1)]
    factor = data.terms[first : t + 1] / as_of
    closes = (data.close[first : t + 1] * factor).tolist()
    volumes = (data.volume[first : t + 1] / factor).tolist()
    return closes, volumes


def _returns(closes: list[float]) -> list[float]:
    # As CompositeRuleStrategy._build_execution_context builds them.
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]
    ]


WINDOWED = [
    ("simple_moving_average", trend.simple_moving_average, "closes", {"window": 10}),
    ("simple_moving_average", trend.simple_moving_average, "closes", {"window": 50}),
    (
        "distance_from_moving_average",
        mean_reversion.distance_from_moving_average,
        "closes",
        {"window": 20},
    ),
    ("z_score", mean_reversion.z_score, "closes", {"window": 10}),
    ("z_score", mean_reversion.z_score, "closes", {"window": 20}),
    ("momentum", momentum.momentum, "closes", {"lookback": 10}),
    ("rate_of_change", momentum.rate_of_change, "closes", {"lookback": 20}),
    ("rsi", momentum.rsi, "closes", {"window": 14}),
    ("rolling_standard_deviation", volatility.rolling_standard_deviation, "closes", {"window": 20}),
    ("realized_volatility", volatility.realized_volatility, "returns", {"window": 20}),
    ("average_volume", volume.average_volume, "volumes", {"window": 20}),
    ("volume_ratio", volume.volume_ratio, "volumes", {"window": 30}),
]


@pytest.mark.parametrize(("name", "function", "feed", "parameters"), WINDOWED)
def test_column_equals_the_strategy_function_on_every_bar(name, function, feed, parameters):
    data = _history()
    column = screen.indicator_column(name, data, **parameters)
    flat = (
        screen.flat_windows(data, window=parameters["window"])
        if name == "z_score"
        else np.zeros(BARS, dtype=bool)
    )
    compared = 0
    for t in range(BARS):
        closes, volumes = _handed(data, t, CONTEXT)
        handed = {"closes": closes, "volumes": volumes, "returns": _returns(closes)}[feed]
        expected = function(handed, **parameters)
        if flat[t]:
            # The one declared departure: the strategy function is None or rounding
            # noise on a window that did not move; the column leaves the bar out.
            assert math.isnan(column[t])
            continue
        if expected is None:
            assert math.isnan(column[t]), f"{name} bar {t}: column has a value, function None"
            continue
        assert math.isclose(column[t], expected, rel_tol=1e-9, abs_tol=1e-9), (
            f"{name} bar {t}: column {column[t]!r}, function {expected!r}"
        )
        compared += 1
    assert compared > 100


def test_the_history_exercises_the_split_and_the_flat_stretch():
    data = _history()
    assert screen.flat_windows(data, window=20).sum() >= 10
    # A dollar indicator read across the split is in the terms of the bar it trades on:
    # pre-split dollars up to the bar before the split's eve, post-split from the eve.
    sma = screen.simple_moving_average(data, window=10)
    assert sma[SPLIT_AT - 2] == pytest.approx(2 * sma[SPLIT_AT - 1], rel=0.02)
    # A ratio does not notice the split.
    z = screen.z_score(data, window=10)
    assert np.all(np.abs(z[SPLIT_AT - 3 : SPLIT_AT + 3]) < 5)


@pytest.mark.parametrize(("window", "context"), [(10, 10), (10, 51), (50, 51), (20, 120)])
def test_exponential_average_equals_the_function_on_the_bars_it_is_handed(window, context):
    data = _history()
    column = screen.exponential_moving_average(data, window=window, context_bars=context)
    assert np.isnan(column[: context - 1]).all()
    for t in range(context - 1, BARS):
        closes, _ = _handed(data, t, context)
        expected = trend.exponential_moving_average(closes, window)
        assert math.isclose(column[t], expected, rel_tol=1e-9, abs_tol=1e-9), f"bar {t}"


def test_exponential_average_handed_only_its_window_is_the_simple_average():
    data = _history()
    ema = screen.exponential_moving_average(data, window=50)
    sma = screen.simple_moving_average(data, window=50)
    np.testing.assert_allclose(ema[49:], sma[49:], rtol=1e-12)


@pytest.mark.parametrize(("window", "context"), [(14, 29), (14, 80), (5, 51)])
def test_wilder_rsi_equals_the_function_on_the_bars_it_is_handed(window, context):
    data = _history()
    column = screen.rsi_wilder(data, window=window, context_bars=context)
    assert np.isnan(column[: context - 1]).all()
    for t in range(context - 1, BARS):
        closes, _ = _handed(data, t, context)
        expected = momentum.rsi_wilder(closes, window)
        assert math.isclose(column[t], expected, rel_tol=1e-9, abs_tol=1e-9), f"bar {t}"


def test_volume_spike_is_the_function_where_there_is_a_ratio():
    data = _history()
    column = screen.volume_spike(data, window=20, threshold=1.5)
    assert np.isnan(column[:19]).all()
    for t in range(19, BARS):
        _, volumes = _handed(data, t, CONTEXT)
        assert column[t] == float(volume.volume_spike(volumes, 20, 1.5)), f"bar {t}"
    assert 0 < np.nansum(column) < BARS - 19  # both answers occur


def test_every_strategy_indicator_has_a_column():
    names = {
        name
        for module in (mean_reversion, momentum, trend, volatility, volume)
        for name, value in vars(module).items()
        if callable(value) and value.__module__ == module.__name__
    }
    assert names == set(screen.INDICATORS)


def test_a_history_shorter_than_the_window_has_no_value():
    data = screen.IndicatorInput.without_splits(np.array([10.0, 10.5, 11.0]), np.ones(3))
    for name, parameters in [
        ("simple_moving_average", {"window": 5}),
        ("exponential_moving_average", {"window": 5}),
        ("z_score", {"window": 5}),
        ("momentum", {"lookback": 5}),
        ("rate_of_change", {"lookback": 3}),
        ("rsi", {"window": 5}),
        ("rsi_wilder", {"window": 5}),
        ("realized_volatility", {"window": 5}),
        ("volume_ratio", {"window": 5}),
        ("volume_spike", {"window": 5}),
    ]:
        assert np.isnan(screen.indicator_column(name, data, **parameters)).all(), name


def test_bad_settings_are_refused_as_the_functions_refuse_them():
    data = _history()
    with pytest.raises(ValueError, match="window must be positive"):
        screen.z_score(data, window=0)
    with pytest.raises(ValueError, match="lookback must be positive"):
        screen.momentum(data, lookback=0)
    with pytest.raises(ValueError, match="at least window"):
        screen.exponential_moving_average(data, window=20, context_bars=10)
    with pytest.raises(ValueError, match="2 \\* window \\+ 1"):
        screen.rsi_wilder(data, window=14, context_bars=20)
    with pytest.raises(ValueError, match="unknown indicator"):
        screen.indicator_column("macd", data)
    with pytest.raises(ValueError, match="same length"):
        screen.IndicatorInput(close=np.ones(3), volume=np.ones(2), terms=np.ones(3))
