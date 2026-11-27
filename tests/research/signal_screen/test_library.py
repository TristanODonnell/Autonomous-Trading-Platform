"""Rows, conditions and the hash that pins a library."""

from __future__ import annotations

import numpy as np
import pytest

from autonomous_trading_platform.research.signal_screen.indicators import (
    INDICATORS,
    IndicatorInput,
    simple_moving_average,
    z_score,
)
from autonomous_trading_platform.research.signal_screen.libraries import (
    LIBRARIES,
    V1,
    library_named,
)
from autonomous_trading_platform.research.signal_screen.library import (
    UNITS,
    Condition,
    Library,
    Reading,
    Row,
    reading,
)
from autonomous_trading_platform.research.signal_screen.panel import HORIZONS
from autonomous_trading_platform.research.signal_screen.screen import (
    RETURN_KINDS,
    ScreenSettings,
    memory_sessions,
)


def _data(count: int = 80, seed: int = 4) -> IndicatorInput:
    rng = np.random.default_rng(seed)
    closes = np.round(100 + rng.normal(0, 1, count).cumsum(), 2)
    return IndicatorInput.without_splits(closes, rng.integers(100, 900, count).astype(float))


def _row(*conditions: Condition, direction=1, scale="5min", row_id="r", reads="an idea") -> Row:
    return Row(
        id=row_id,
        family="f",
        scale=scale,
        conditions=tuple(conditions),
        direction=direction,
        reads=reads,
    )


LOW = Condition(reading("z_score", window=20), "<", -1.5)


# --------------------------------------------------------------------- readings


def test_a_reading_names_its_indicator_settings_reach_and_unit():
    z = reading("z_score", window=20)
    assert z == Reading("z_score", (("window", 20),))
    assert (z.label, z.bars, z.unit) == ("z_score(window=20)", 20, "none")
    ema = reading("exponential_moving_average", window=10, context_bars=51)
    assert ema.label == "exponential_moving_average(context_bars=51, window=10)"
    assert (ema.bars, ema.unit) == (51, "dollars")
    assert reading("rate_of_change", lookback=5).bars == 5
    assert reading("volume_spike", window=20, threshold=2.0).bars == 20
    with pytest.raises(ValueError, match="unknown indicator"):
        reading("macd", window=5)
    with pytest.raises(ValueError, match="name order"):
        Reading("z_score", (("window", 20), ("a", 1)))


def test_every_indicator_has_a_unit():
    assert set(UNITS) == set(INDICATORS)


def test_an_offset_reads_the_value_one_decision_earlier():
    data = _data()
    now = reading("simple_moving_average", window=10)
    before = reading("simple_moving_average", window=10, offset=-1)
    np.testing.assert_array_equal(now.column(data), simple_moving_average(data, window=10))
    np.testing.assert_array_equal(before.column(data)[1:], now.column(data)[:-1])
    assert np.isnan(before.column(data)[0])
    assert before.label == "simple_moving_average(window=10)[-1]"
    assert before.bars == 11
    assert np.isnan(reading("z_score", window=3, offset=-200).column(data)).all()
    with pytest.raises(ValueError, match="looks back"):
        reading("z_score", window=3, offset=1)


# --------------------------------------------------------------------- conditions


def test_a_condition_is_one_zero_or_no_value():
    data = _data()
    z = z_score(data, window=20)
    held = LOW.column(data)
    assert np.isnan(held[:19]).all()
    np.testing.assert_array_equal(held[19:], (z[19:] < -1.5).astype(float))
    assert LOW.label == "z_score(window=20) < -1.5"
    # Every operator, on a reading against a number.
    for symbol, compare in ((">", np.greater), ("<=", np.less_equal), (">=", np.greater_equal)):
        column = Condition(reading("z_score", window=20), symbol, 0.5).column(data)
        np.testing.assert_array_equal(column[19:], compare(z[19:], 0.5).astype(float))
    with pytest.raises(ValueError, match="an operator is one of"):
        Condition(reading("z_score", window=20), "==", 0.0)


def test_a_condition_between_two_readings_needs_both_values():
    data = _data()
    fast = reading("simple_moving_average", window=5)
    slow = reading("simple_moving_average", window=30)
    held = Condition(fast, ">", slow).column(data)
    assert np.isnan(held[:29]).all() and not np.isnan(held[29:]).any()
    expected = simple_moving_average(data, window=5) > simple_moving_average(data, window=30)
    np.testing.assert_array_equal(held[29:], expected[29:].astype(float))
    assert Condition(fast, ">", slow).readings == (fast, slow)


def test_dollars_and_shares_are_compared_with_zero_or_their_own_kind_only():
    sma = reading("simple_moving_average", window=10)
    Condition(reading("distance_from_moving_average", window=20), ">", 0.0)  # a sign
    Condition(sma, ">", reading("exponential_moving_average", window=10))  # dollars both
    Condition(reading("average_volume", window=5), ">", reading("average_volume", window=20))
    with pytest.raises(ValueError, match="differ from name to name"):
        Condition(reading("rolling_standard_deviation", window=20), "<", 5.0)
    with pytest.raises(ValueError, match="cannot be compared"):
        Condition(sma, ">", reading("z_score", window=10))
    with pytest.raises(ValueError, match="cannot be compared"):
        Condition(sma, ">", reading("average_volume", window=10))


# --------------------------------------------------------------------- rows


def test_a_row_holds_where_every_condition_holds():
    data = _data(200)
    busy = Condition(reading("volume_ratio", window=30), ">", 1.0)
    row = _row(LOW, busy)
    low, high_volume, both = LOW.column(data), busy.column(data), row.column(data)
    assert np.isnan(both[:29]).all()  # the slower reading has no value yet
    np.testing.assert_array_equal(both[29:], ((low[29:] > 0) & (high_volume[29:] > 0)) * 1.0)
    assert 0 < both[29:].sum() < low[29:].sum()
    assert row.label == "z_score(window=20) < -1.5 and volume_ratio(window=30) > 1"
    assert row.bars == 30 and row.predicts == "higher"
    assert _row(LOW, direction=-1).predicts == "lower"
    assert row.describe() == {
        "id": "r",
        "family": "f",
        "scale": "5min",
        "condition": row.label,
        "predicts": "higher",
        "reads": "an idea",
    }


def test_a_row_is_refused_without_an_id_a_condition_or_a_known_scale_and_direction():
    with pytest.raises(ValueError, match="at least one condition"):
        _row()
    with pytest.raises(ValueError, match="at least one condition"):
        _row(LOW, row_id="")
    with pytest.raises(ValueError, match="a scale is"):
        _row(LOW, scale="hourly")
    with pytest.raises(ValueError, match="direction is"):
        _row(LOW, direction=0)


def test_memory_is_counted_in_sessions_of_the_rows_scale():
    slow = Condition(
        reading("simple_moving_average", window=100),
        ">",
        reading("simple_moving_average", window=5),
    )
    assert memory_sessions(_row(slow, scale="5min")) == 2  # 100 bars of 78 a session
    assert memory_sessions(_row(slow, scale="daily")) == 100
    assert memory_sessions(_row(LOW, scale="5min")) == 1


# --------------------------------------------------------------------- libraries


def test_a_librarys_hash_covers_what_is_tested_and_not_the_prose():
    base = Library("t", (_row(LOW),))
    assert base.content_hash() == Library("t", (_row(LOW),)).content_hash()
    assert base.content_hash() == Library("t", (_row(LOW, reads="other words"),)).content_hash()
    different = [
        Library("t2", (_row(LOW),)),
        Library("t", (_row(LOW, row_id="other"),)),
        Library("t", (_row(LOW, direction=-1),)),
        Library("t", (_row(LOW, scale="daily"),)),
        Library("t", (_row(Condition(reading("z_score", window=20), "<", -2.0)),)),
        Library("t", (_row(Condition(reading("z_score", window=10), "<", -1.5)),)),
        Library("t", (_row(Condition(reading("z_score", window=20), "<=", -1.5)),)),
        Library("t", (_row(Condition(reading("z_score", window=20, offset=-1), "<", -1.5)),)),
        Library("t", (_row(LOW), _row(LOW, row_id="second"))),
    ]
    hashes = {library.content_hash() for library in different}
    assert len(hashes) == len(different) and base.content_hash() not in hashes


def test_a_library_is_refused_empty_or_with_an_id_used_twice():
    with pytest.raises(ValueError, match="at least one row"):
        Library("t", ())
    with pytest.raises(ValueError, match=r"repeated: \['r'\]"):
        Library("t", (_row(LOW), _row(LOW, direction=-1)))
    library = Library("t", (_row(LOW), _row(LOW, row_id="d", scale="daily")))
    assert [row.id for row in library.on_scale("daily")] == ["d"]
    assert library.describe()["rows"][0]["id"] == "r"
    assert library.describe()["hash"] == library.content_hash()


def test_libraries_are_found_by_name():
    assert library_named("v1") is V1 and LIBRARIES == {"v1": V1}
    with pytest.raises(ValueError, match="no library named 'v9'"):
        library_named("v9")


# --------------------------------------------------------------------- v1


def test_v1_is_the_library_the_user_approved():
    """Approved as drafted by the user on 2026-11-27, before any run (research plan,
    "Signal screen, stage 1", row 5). The hash covers every row's id, scale, conditions
    and direction, so a row changed, added or taken out fails here. That is the point:
    a variation is a new library (v2) beside this one, never an edit to it."""
    assert V1.content_hash() == ("efede93d515ab23c0a09516d4ef36302a5a85b65f57fe5ff370bdccd7df46924")
    assert len(V1.rows) == 80


def test_v1_asks_the_same_questions_on_both_scales():
    five = [row.id.removeprefix("5m.") for row in V1.on_scale("5min")]
    daily = [row.id.removeprefix("d.") for row in V1.on_scale("daily")]
    assert five == daily and len(five) == 40
    for scale, prefix in (("5min", "5m."), ("daily", "d.")):
        assert all(row.id.startswith(prefix) for row in V1.on_scale(scale))
    # Every row computes on plain bars, and none reaches further back than 101.
    data = _data(300)
    for row in V1.rows:
        column = row.column(data)
        assert set(np.unique(column[~np.isnan(column)])) <= {0.0, 1.0}, row.id
        assert np.isfinite(column).any(), row.id
        assert row.bars <= 101


def test_v1_reads_twelve_of_the_thirteen_indicators():
    read = {r.indicator for row in V1.rows for r in row.readings}
    # Left out, with the reasons in libraries.py.
    assert set(INDICATORS) - read == {"momentum", "rolling_standard_deviation"}


def test_v1_tests_a_crossover_as_the_generator_trades_it():
    row = next(row for row in V1.rows if row.id == "5m.ema_10x50_up")
    now, before = row.conditions
    # A composite hands both averages 51 bars now, and 50 for the bar before.
    assert dict(now.left.parameters) == {"window": 10, "context_bars": 51}
    assert dict(now.right.parameters) == {"window": 50, "context_bars": 51}
    assert (now.operator, before.operator) == (">", "<=")
    assert dict(before.right.parameters) == {"window": 50, "context_bars": 50}
    assert before.left.offset == before.right.offset == -1
    down = next(row for row in V1.rows if row.id == "5m.sma_10x50_down")
    assert [c.operator for c in down.conditions] == ["<", ">="] and down.direction == -1


def test_the_declared_shuffles_can_resolve_the_first_threshold_of_v1():
    """Benjamini-Hochberg's smallest threshold is q / cells. A p-value cannot be under
    1 / (shuffles + 1), so with too few shuffles nothing could ever pass."""
    settings = ScreenSettings()
    cells = sum(len(HORIZONS[row.scale]) * len(RETURN_KINDS) for row in V1.rows)
    assert cells == 720
    assert 1 / (settings.shuffles + 1) < settings.q / cells
