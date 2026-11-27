"""Bars to decisions, the bar each trades on, and the return that follows."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from autonomous_trading_platform.accounting.corporate_actions import split_factor_for
from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.research.signal_screen.bars import (
    build_name_bars,
    session_final_times,
)
from autonomous_trading_platform.research.signal_screen.indicators import z_score
from autonomous_trading_platform.research.signal_screen.panel import (
    HORIZONS,
    Horizon,
    build_panel,
    daily_decisions,
    five_minute_decisions,
    forward_grid,
    forward_returns,
    parse_horizon,
)

NEW_YORK = ZoneInfo("America/New_York")
D1, D2, D3 = date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)  # winter
SUMMER = date(2025, 7, 7)
SESSIONS = [D1, D2, D3, SUMMER]
LAST_SLOT = 77


def _at(day: date, slot: int) -> datetime:
    opened = datetime.combine(day, time(9, 30), tzinfo=NEW_YORK)
    return (opened + timedelta(minutes=5 * slot)).astimezone(UTC)


def _bars(symbol, rows, actions=(), sessions=SESSIONS):
    """rows: (day, slot, close) or (day, slot, close, volume)."""
    return build_name_bars(
        symbol=symbol,
        timestamps=[_at(row[0], row[1]) for row in rows],
        close=[row[2] for row in rows],
        volume=[row[3] if len(row) > 3 else 100.0 for row in rows],
        actions=list(actions),
        sessions=sessions,
    )


def _action(symbol: str, kind: CorporateActionType, day: date, **amount) -> CorporateAction:
    return CorporateAction(
        action_id=f"{symbol}-{kind.value}-{day}",
        symbol=symbol,
        action_type=kind,
        effective_date=day,
        currency="USD",
        new_symbol=symbol,
        source="test",
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
        **amount,
    )


def _split(symbol: str, day: date, ratio: str) -> CorporateAction:
    return _action(symbol, CorporateActionType.SPLIT_FORWARD, day, split_ratio=Decimal(ratio))


def _dividend(symbol: str, day: date, per_share: str) -> CorporateAction:
    return _action(symbol, CorporateActionType.CASH_DIVIDEND, day, cash_amount=Decimal(per_share))


# --------------------------------------------------------------------- bars


def test_a_bar_gets_its_session_and_the_same_slot_in_winter_and_summer():
    bars = _bars("A", [(D1, 0, 10), (D1, LAST_SLOT, 11), (SUMMER, 0, 12), (SUMMER, LAST_SLOT, 13)])
    assert bars.session.tolist() == [0, 0, 3, 3]
    assert bars.slot.tolist() == [0, LAST_SLOT, 0, LAST_SLOT]
    # 09:30 New York is 14:30 UTC in January and 13:30 in July.
    hours = bars.timestamps.astype("datetime64[h]").astype(int) % 24
    assert hours[0] == 14 and hours[2] == 13
    assert bars.session_last_bar.tolist() == [1, -1, -1, 3]
    assert bars.bars_not_read == 0


def test_bars_off_the_sessions_or_outside_the_slots_are_not_read():
    saturday = date(2025, 1, 11)
    stamps = [
        _at(D1, 0),
        _at(D1, 0),  # the same bar again: the later row is kept
        _at(D1, -6),  # 09:00, before the open
        _at(D1, 78),  # 16:00, after the last slot
        _at(D1, 1) + timedelta(minutes=2),  # 09:37, not on the 5-minute grid
        _at(saturday, 3),  # not a session
        _at(D1, 2),
    ]
    bars = build_name_bars(
        symbol="A",
        timestamps=stamps,
        close=[10, 11, 1, 1, 1, 1, 12],
        volume=[5] * 7,
        actions=[],
        sessions=SESSIONS,
    )
    assert bars.close.tolist() == [11.0, 12.0]
    assert bars.slot.tolist() == [0, 2]
    assert bars.bars_not_read == 5


def test_terms_are_the_accounting_split_factor_and_restate_a_continuous_price():
    split = _split("A", D3, "2")
    bars = _bars("A", [(D1, 0, 100), (D2, 0, 104), (D3, 0, 53)], actions=[split])
    assert bars.terms.tolist() == [0.5, 0.5, 1.0]
    for day, terms in zip([D1, D2, D3], bars.terms, strict=True):
        assert terms == float(split_factor_for([split], bar_date=day, as_of=D3))
    assert bars.restated_close.tolist() == [50.0, 52.0, 53.0]


def test_another_names_actions_are_ignored():
    bars = _bars("A", [(D1, 0, 100), (D3, 0, 101)], actions=[_split("B", D2, "2")])
    assert bars.terms.tolist() == [1.0, 1.0]


def test_dividends_paid_count_from_the_ex_date_in_the_terms_of_the_last_bar():
    actions = [_dividend("A", D2, "0.50"), _split("A", D3, "2")]
    bars = _bars("A", [(D1, 0, 100), (D2, 0, 100), (D2, 5, 100), (D3, 0, 50)], actions=actions)
    # 0.50 a share before the 2-for-1 is 0.25 a share after it.
    assert bars.dividends_paid.tolist() == [0.0, 0.25, 0.25, 0.25]


def test_the_final_tick_of_a_session_is_the_last_bar_of_any_name():
    a = _bars("A", [(D1, 0, 1), (D1, 40, 1), (D2, 0, 1)])
    b = _bars("B", [(D1, 0, 1), (D1, LAST_SLOT, 1)])
    final = session_final_times([a, b], len(SESSIONS))
    assert final[0] == np.datetime64(_at(D1, LAST_SLOT).replace(tzinfo=None))
    assert final[1] == np.datetime64(_at(D2, 0).replace(tzinfo=None))
    assert np.isnat(final[2])


# --------------------------------------------------------------------- 5-minute scale


def _one_session(closes, symbol="A", day=D1, slots=None):
    slots = range(len(closes)) if slots is None else slots
    return _bars(symbol, [(day, slot, close) for slot, close in zip(slots, closes, strict=True)])


def test_a_five_minute_decision_trades_on_the_names_next_bar():
    bars = _one_session([100, 101, 102])
    decisions = five_minute_decisions(bars)
    assert decisions.entry_bar.tolist() == [1, 2, -1]
    assert decisions.indicator_input.close.tolist() == [100.0, 101.0, 102.0]


def test_a_bars_horizon_is_measured_from_the_entry_to_that_many_bars_later():
    bars = _one_session([100, 101, 102, 103, 104])
    final = session_final_times([bars], len(SESSIONS))
    entry = five_minute_decisions(bars).entry_bar
    returns, stale = forward_returns(bars, entry, Horizon("bars", 1), final)
    # Decision on bar 0 trades at 101 and is measured at 102, and so on.
    assert returns[:3] == pytest.approx([102 / 101 - 1, 103 / 102 - 1, 104 / 103 - 1])
    # Bar 3 trades on the session's last bar: one bar later is past the final tick.
    assert np.isnan(returns[3]) and np.isnan(returns[4])
    assert not stale.any()
    three, _ = forward_returns(bars, entry, Horizon("bars", 3), final)
    assert three[0] == pytest.approx(104 / 101 - 1)
    assert np.isnan(three[1:]).all()


def test_a_missing_bar_at_the_horizon_reads_the_last_close_before_it():
    # No bar in slot 3: the 2-bar horizon from the entry in slot 1 reads slot 2.
    bars = _one_session([100, 101, 102, 105, 106], slots=[0, 1, 2, 4, 5])
    final = session_final_times([bars], len(SESSIONS))
    entry = five_minute_decisions(bars).entry_bar
    returns, stale = forward_returns(bars, entry, Horizon("bars", 2), final)
    assert returns[0] == pytest.approx(102 / 101 - 1)
    assert not stale[0]
    # The entry in slot 2 has no later bar by slot 3: nothing traded, return 0, stale.
    one, stale_one = forward_returns(bars, entry, Horizon("bars", 1), final)
    assert one[1] == 0.0 and stale_one[1]


def test_a_decision_on_the_last_bar_of_a_session_trades_on_the_next_sessions_first():
    bars = _bars("A", [(D1, 0, 100), (D1, 1, 101), (D2, 0, 110), (D2, 1, 111), (D2, 2, 112)])
    final = session_final_times([bars], len(SESSIONS))
    entry = five_minute_decisions(bars).entry_bar
    assert entry[1] == 2  # read D1's last bar, trades on D2's first
    returns, _ = forward_returns(bars, entry, Horizon("bars", 1), final)
    assert returns[1] == pytest.approx(111 / 110 - 1)  # the overnight gap is not in it


def test_a_sessions_horizon_reads_the_names_last_bar_of_that_session():
    bars = _bars(
        "A",
        [(D1, 0, 100), (D1, 1, 101), (D1, 2, 103), (D2, 0, 104), (D2, 9, 106), (SUMMER, 0, 120)],
    )
    final = session_final_times([bars], len(SESSIONS))
    entry = five_minute_decisions(bars).entry_bar
    to_close, _ = forward_returns(bars, entry, Horizon("sessions", 0), final)
    assert to_close[0] == pytest.approx(103 / 101 - 1)
    assert np.isnan(to_close[1])  # trades on the session's last bar: nothing after it
    next_close, _ = forward_returns(bars, entry, Horizon("sessions", 1), final)
    assert next_close[0] == pytest.approx(106 / 101 - 1)
    # From D2 the next session is D3, where the name has no bar: not measured.
    assert np.isnan(next_close[2])
    # Two sessions on from D1 is D3 (no bar); three on is SUMMER.
    two, _ = forward_returns(bars, entry, Horizon("sessions", 2), final)
    assert np.isnan(two[0])
    three, _ = forward_returns(bars, entry, Horizon("sessions", 3), final)
    assert three[0] == pytest.approx(120 / 101 - 1)
    # Four on is past the last session loaded: nothing is measured.
    assert np.isnan(forward_returns(bars, entry, Horizon("sessions", 4), final)[0]).all()


def test_the_return_is_total_across_a_split_and_a_dividend():
    actions = [_split("A", D2, "2"), _dividend("A", D3, "0.30")]
    bars = _bars(
        "A",
        [(D1, 0, 99), (D1, 1, 100), (D2, 0, 51), (D3, 0, 52), (D3, 1, 52)],
        actions=actions,
    )
    final = session_final_times([bars], len(SESSIONS))
    entry = five_minute_decisions(bars).entry_bar
    two_sessions, _ = forward_returns(bars, entry, Horizon("sessions", 2), final)
    # Bought at 100 before the 2-for-1 (50 a new share), ends at 52 with 0.30 received.
    assert two_sessions[0] == pytest.approx((52 + 0.30) / 50 - 1)
    # Bought on the ex-date itself: no dividend.
    same_day, _ = forward_returns(bars, entry, Horizon("sessions", 0), final)
    assert same_day[2] == pytest.approx(52 / 52 - 1)


def test_horizon_keys_and_labels():
    assert [h.label for h in HORIZONS["5min"]] == [
        "5 minutes",
        "15 minutes",
        "1 hour",
        "to the close",
        "1 session",
        "5 sessions",
    ]
    assert parse_horizon("bars:12") == Horizon("bars", 12)
    assert parse_horizon(Horizon("sessions", 5).key) == Horizon("sessions", 5)
    with pytest.raises(ValueError, match="horizon is"):
        parse_horizon("days:3")
    with pytest.raises(ValueError, match="at least one bar"):
        Horizon("bars", 0)


# --------------------------------------------------------------------- daily scale


def test_a_daily_decision_reads_up_to_the_last_tick_and_trades_on_the_final_bar():
    a = _bars(
        "A",
        [
            (D1, 0, 100, 10),
            (D1, 40, 101, 20),
            (D1, 76, 102, 30),
            (D1, LAST_SLOT, 103, 40),
            (D2, 0, 104, 5),
            (D2, 70, 105, 6),  # no bar at D2's final tick
            (D3, LAST_SLOT, 106, 7),  # nothing before D3's final tick
        ],
    )
    b = _bars("B", [(D1, LAST_SLOT, 1), (D2, LAST_SLOT, 1), (D3, LAST_SLOT, 1)])
    final = session_final_times([a, b], len(SESSIONS))
    decisions = daily_decisions(a, final)
    assert decisions.session.tolist() == [0, 1]  # no decision on D3
    assert decisions.indicator_input.close.tolist() == [102.0, 105.0]
    assert decisions.indicator_input.volume.tolist() == [60.0, 11.0]
    assert decisions.entry_bar.tolist() == [3, -1]
    assert decisions.slot.tolist() == [0, 0]
    # The final bar is on the decision's own day, so the terms are the day's own.
    np.testing.assert_array_equal(
        decisions.indicator_input.decision_terms, decisions.indicator_input.terms
    )
    one_session, _ = forward_returns(a, decisions.entry_bar, Horizon("sessions", 1), final)
    assert one_session[0] == pytest.approx(105 / 103 - 1)
    assert np.isnan(one_session[1])


# --------------------------------------------------------------------- the grid


def _six_names(third_close):
    """Six names, three sessions, two bars a session at the first and last slot."""
    names = []
    for index, symbol in enumerate("ABCDEF"):
        rows = []
        for day, level in zip([D1, D2, D3], [100.0, 110.0, third_close[index]], strict=True):
            rows += [(day, 0, level), (day, LAST_SLOT, level)]
        names.append(_bars(symbol, rows, sessions=[D1, D2, D3]))
    return names


def test_the_grid_holds_counted_decisions_and_the_excess_is_over_names_in_force():
    third = [121.0, 121.0, 121.0, 121.0, 121.0, 99.0]
    names = _six_names(third)
    in_force = np.ones((3, 6), dtype=bool)
    panel = build_panel(
        scale="daily", names=names, sessions=[D1, D2, D3], first_observed=1, in_force=in_force
    )
    assert panel.shape == (2, 1, 6)
    assert panel.observed_sessions == (D2, D3)
    grid = forward_grid(panel, Horizon("sessions", 1))
    # D2's decisions trade at 110 and are measured at D3's close.
    raw = np.array(third) / 110 - 1
    np.testing.assert_allclose(grid.raw[0, 0], raw)
    np.testing.assert_allclose(grid.excess[0, 0], raw - raw.mean())
    assert np.isnan(grid.raw[1]).all()  # D3 is the last session loaded
    assert grid.measured == 6 and grid.stale == 0

    # F leaves the list on D2: its decision that day is not counted, and the market
    # the others are compared with no longer holds it.
    in_force[1, 5] = False
    panel = build_panel(
        scale="daily", names=names, sessions=[D1, D2, D3], first_observed=1, in_force=in_force
    )
    grid = forward_grid(panel, Horizon("sessions", 1))
    assert np.isnan(grid.raw[0, 0, 5])
    np.testing.assert_allclose(grid.excess[0, 0, :5], 0.0, atol=1e-12)


def test_fewer_names_than_a_market_needs_leaves_the_excess_out():
    names = _six_names([121.0] * 6)[:4]
    panel = build_panel(
        scale="daily",
        names=names,
        sessions=[D1, D2, D3],
        first_observed=0,
        in_force=np.ones((3, 4), dtype=bool),
    )
    grid = forward_grid(panel, Horizon("sessions", 1))
    assert np.isfinite(grid.raw[0, 0]).all()
    assert np.isnan(grid.excess).all()


def test_warmup_sessions_feed_the_indicator_and_stay_off_the_grid():
    names = _six_names([121.0] * 6)
    panel = build_panel(
        scale="5min",
        names=names,
        sessions=[D1, D2, D3],
        first_observed=1,
        in_force=np.ones((3, 6), dtype=bool),
    )
    assert panel.shape == (2, 78, 6)
    # Every bar is a decision the indicator reads, D1's included.
    assert len(panel.decisions[0]) == 6
    column = np.arange(6, dtype=float)
    grid = panel.grid([column] * 6)
    assert grid[0, 0, 0] == 2.0 and grid[0, LAST_SLOT, 0] == 3.0  # D2's two bars
    assert grid[1, 0, 0] == 4.0
    assert np.isnan(grid[1, LAST_SLOT, 0])  # the last bar has nothing to trade on
    assert np.isfinite(grid).sum() == 3 * 6
    with pytest.raises(ValueError, match="one column per name"):
        panel.grid([column])
    with pytest.raises(ValueError, match="one value per decision"):
        panel.grid([column[:2]] * 6)


def test_build_panel_refuses_a_membership_table_of_the_wrong_shape():
    names = _six_names([121.0] * 6)
    with pytest.raises(ValueError, match="sessions x names"):
        build_panel(
            scale="daily",
            names=names,
            sessions=[D1, D2, D3],
            first_observed=0,
            in_force=np.ones((2, 6), dtype=bool),
        )
    with pytest.raises(ValueError, match="first_observed"):
        build_panel(
            scale="daily",
            names=names,
            sessions=[D1, D2, D3],
            first_observed=3,
            in_force=np.ones((3, 6), dtype=bool),
        )


# --------------------------------------------------------------------- no look-ahead


def test_nothing_after_a_decisions_exit_changes_its_indicator_or_its_return():
    rng = np.random.default_rng(3)
    rows = [
        (day, slot, float(np.round(100 + rng.normal(0, 1), 2)))
        for day in (D1, D2, D3)
        for slot in range(0, 78, 2)
    ]
    cut = 100  # bars from here on (inside D3) are changed
    changed = [
        (day, slot, close if index < cut else close + 2.0 * (index % 5))
        for index, (day, slot, close) in enumerate(rows)
    ]
    before, after = _bars("A", rows), _bars("A", changed)
    final = session_final_times([before], len(SESSIONS))

    z_before = z_score(five_minute_decisions(before).indicator_input, window=10)
    z_after = z_score(five_minute_decisions(after).indicator_input, window=10)
    np.testing.assert_array_equal(z_before[:cut], z_after[:cut])
    assert not np.allclose(z_before[cut + 10 :], z_after[cut + 10 :], equal_nan=True)

    entry = five_minute_decisions(before).entry_bar
    for horizon in (Horizon("bars", 2), Horizon("sessions", 0), Horizon("sessions", 1)):
        r_before, _ = forward_returns(before, entry, horizon, final)
        r_after, _ = forward_returns(after, entry, horizon, final)
        if horizon.kind == "bars":
            # 2 bars of 5 minutes is one bar of this 10-minute history.
            exit_bar = np.minimum(entry + 1, len(rows) - 1)
        else:
            exit_bar = before.session_last_bar[
                np.minimum(before.session[np.maximum(entry, 0)] + horizon.count, len(SESSIONS) - 1)
            ]
        untouched = (entry >= 0) & (exit_bar < cut) & np.isfinite(r_before)
        assert untouched.sum() > 5
        np.testing.assert_array_equal(r_before[untouched], r_after[untouched])
