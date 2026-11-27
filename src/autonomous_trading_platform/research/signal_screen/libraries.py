"""The libraries a screen can be run with, by name.

A library is fixed once it is approved: its hash is pinned by a test
(``tests/research/signal_screen/test_library.py``), and a change of mind is a new
library beside it, not an edit. v1 was approved by the user on 2026-11-27, before any
run.

**v1** asks one question of the indicators the platform has today: after a bar where
one of them says something, is the return different from other days, at any horizon?
Its rows are the conditions the strategy generator trades on
(``research/strategy_generation/composite_generation.py``: the same indicators,
windows and thresholds) and the plain families beside them (RSI levels, volume and
volatility against their own average), each on 5-minute bars and on daily closes.

How the rows are chosen:

- **As traded.** Thresholds are the generator's (z-score at 1.5 and 2, volume ratio at
  1.0 and 1.2, the sign of a rate of change). A crossover is the event its rule fires
  on: at or below one bar earlier, above now. Exponential averages are handed the bar
  count a composite hands them (the slow window plus one), and Wilder's RSI its own.
- **Both sides are separate rows.** "Low z-score, then higher" and "high z-score, then
  lower" are two ideas; either can hold without the other. A "lower" row is a reason
  to be out, not a short: the platform does not sell short.
- **No direction of its own, both ways.** Volume and volatility say nothing about up
  or down by themselves, so each is asked both questions.
- **Left out, with the reason.** ``momentum``: the same sign as the rate of change over
  the same bars, so the row would be a copy. ``z_score > 0``: the same as the price
  above its average. ``rolling_standard_deviation``: in dollars, so one threshold means
  different things for a 20 dollar and a 900 dollar name (the generator's filter,
  "under 5.0", is that); its unit-free forms are the z-score and realized volatility.
  "SMA against EMA at the same window": handed the same bars the two are the same
  number, so the condition never holds.
"""

from __future__ import annotations

from .library import Condition, Library, Reading, Row, reading
from .panel import Scale
from .statistics import Direction


def _sma(window: int, offset: int = 0) -> Reading:
    return reading("simple_moving_average", window=window, offset=offset)


def _ema(window: int, *, slow: int, offset: int = 0) -> Reading:
    # A crossover composite hands every indicator its slow window plus one bar, and
    # the bar before sees one fewer.
    return reading(
        "exponential_moving_average",
        window=window,
        context_bars=slow + 1 + offset,
        offset=offset,
    )


def _crosses(fast: Reading, slow: Reading, fast_before: Reading, slow_before: Reading, up: bool):
    if up:
        return (Condition(fast, ">", slow), Condition(fast_before, "<=", slow_before))
    return (Condition(fast, "<", slow), Condition(fast_before, ">=", slow_before))


def _v1_rows(scale: Scale) -> list[Row]:
    prefix = "5m" if scale == "5min" else "d"
    bar = "bars" if scale == "5min" else "days"
    rows: list[Row] = []

    def add(name: str, family: str, direction: Direction, reads: str, *conditions: Condition):
        rows.append(
            Row(
                id=f"{prefix}.{name}",
                family=family,
                scale=scale,
                conditions=tuple(conditions),
                direction=direction,
                reads=reads,
            )
        )

    # ------------------------------------------------------------ continuation
    for lookback in (10, 20):
        change = reading("rate_of_change", lookback=lookback)
        add(
            f"roc{lookback}_up",
            "continuation",
            1,
            f"up over the last {lookback} {bar}: it keeps rising",
            Condition(change, ">", 0.0),
        )
        add(
            f"roc{lookback}_down",
            "continuation",
            -1,
            f"down over the last {lookback} {bar}: it keeps falling",
            Condition(change, "<", 0.0),
        )

    # ------------------------------------------------------------ above its average
    for window in (20, 50):
        distance = reading("distance_from_moving_average", window=window)
        add(
            f"above_sma{window}",
            "above its average",
            1,
            f"above its {window}-{bar[:-1]} average: an uptrend goes on",
            Condition(distance, ">", 0.0),
        )
        add(
            f"below_sma{window}",
            "above its average",
            -1,
            f"below its {window}-{bar[:-1]} average: a downtrend goes on",
            Condition(distance, "<", 0.0),
        )

    # ------------------------------------------------------------ reversion
    for window in (10, 20):
        z = reading("z_score", window=window)
        for level, tag in ((1.5, "15"), (2.0, "20")):
            add(
                f"z{window}_low_{tag}",
                "reversion",
                1,
                f"{level:g} deviations under its {window}-{bar[:-1]} average: it comes back up",
                Condition(z, "<", -level),
            )
            add(
                f"z{window}_high_{tag}",
                "reversion",
                -1,
                f"{level:g} deviations over its {window}-{bar[:-1]} average: it comes back down",
                Condition(z, ">", level),
            )

    # ------------------------------------------------------------ crossover
    for up in (True, False):
        way = "up" if up else "down"
        direction: Direction = 1 if up else -1
        then = "rises" if up else "falls"
        verb = "above" if up else "below"
        add(
            f"sma_10x50_{way}",
            "crossover",
            direction,
            f"the 10-{bar[:-1]} average crosses {verb} the 50: it {then}",
            *_crosses(_sma(10), _sma(50), _sma(10, -1), _sma(50, -1), up),
        )
        add(
            f"ema_10x50_{way}",
            "crossover",
            direction,
            f"the 10-{bar[:-1]} exponential average crosses {verb} the 50: it {then}",
            *_crosses(
                _ema(10, slow=50),
                _ema(50, slow=50),
                _ema(10, slow=50, offset=-1),
                _ema(50, slow=50, offset=-1),
                up,
            ),
        )
        for fast, slow in ((10, 50), (20, 100)):
            add(
                f"sma_{fast}{'over' if up else 'under'}{slow}",
                "crossover",
                direction,
                f"the {fast}-{bar[:-1]} average is {verb} the {slow}: it {then}",
                Condition(_sma(fast), ">" if up else "<", _sma(slow)),
            )

    # ------------------------------------------------------------ RSI
    plain = reading("rsi", window=14)
    wilder = reading("rsi_wilder", window=14, context_bars=29)
    for tag, value in (("rsi14", plain), ("wilder14", wilder)):
        add(
            f"{tag}_low",
            "RSI",
            1,
            "RSI at or under 30 (oversold): it rises",
            Condition(value, "<=", 30.0),
        )
        add(
            f"{tag}_high",
            "RSI",
            -1,
            "RSI at or over 70 (overbought): it falls",
            Condition(value, ">=", 70.0),
        )

    # ------------------------------------------------------------ volume
    ratio = reading("volume_ratio", window=20)
    spike = reading("volume_spike", window=20, threshold=2.0)
    rising = Condition(
        reading("average_volume", window=10), ">", reading("average_volume", window=50)
    )
    add(
        "vol20_above_avg_up",
        "volume",
        1,
        "volume above its 20-bar average (a buy in the generator)",
        Condition(ratio, ">", 1.0),
    )
    both_ways: tuple[tuple[Direction, str], ...] = ((1, "up"), (-1, "down"))
    for direction, way in both_ways:
        then = "higher" if direction == 1 else "lower"
        add(
            f"vol20_strong_{way}",
            "volume",
            direction,
            f"volume over 1.2 times its average: the return after is {then}",
            Condition(ratio, ">", 1.2),
        )
        add(
            f"vol_spike_{way}",
            "volume",
            direction,
            f"volume at twice its average or more: the return after is {then}",
            Condition(spike, ">", 0.0),
        )
        add(
            f"vol_rising_{way}",
            "volume",
            direction,
            f"the 10-{bar[:-1]} average volume is above the 50: the return after is {then}",
            rising,
        )

    # ------------------------------------------------------------ volatility
    busier = Condition(
        reading("realized_volatility", window=20), ">", reading("realized_volatility", window=100)
    )
    add(
        "rvol_high_up",
        "volatility",
        1,
        f"more volatile over 20 {bar} than over 100: the return after is higher",
        busier,
    )
    add(
        "rvol_high_down",
        "volatility",
        -1,
        f"more volatile over 20 {bar} than over 100: the return after is lower",
        busier,
    )

    # ------------------------------------------------------------ two conditions
    add(
        "z20_low_15_in_uptrend",
        "two conditions",
        1,
        "a dip in an uptrend: 1.5 deviations under the 20, above the 50 (the regime gate)",
        Condition(reading("z_score", window=20), "<", -1.5),
        Condition(reading("distance_from_moving_average", window=50), ">", 0.0),
    )
    add(
        "z20_low_15_on_volume",
        "two conditions",
        1,
        "a dip on strong volume: 1.5 deviations under the 20, volume over 1.2 times average",
        Condition(reading("z_score", window=20), "<", -1.5),
        Condition(ratio, ">", 1.2),
    )
    add(
        "roc10_up_on_volume",
        "two conditions",
        1,
        "a rise on volume: up over 10, volume above average (the volume confirmation)",
        Condition(reading("rate_of_change", lookback=10), ">", 0.0),
        Condition(ratio, ">", 1.0),
    )
    return rows


V1 = Library(name="v1", rows=tuple(_v1_rows("5min") + _v1_rows("daily")))

LIBRARIES: dict[str, Library] = {V1.name: V1}


def library_named(name: str) -> Library:
    try:
        return LIBRARIES[name]
    except KeyError:
        raise ValueError(f"no library named {name!r}; there is {sorted(LIBRARIES)}") from None
