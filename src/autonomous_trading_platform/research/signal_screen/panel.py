"""Decisions, the bar each trades on, and the return that follows.

A **decision** is one reading of an indicator: the last bar it read, and the bar a
strategy acting on it trades on. Two scales:

- ``5min``: every bar is a decision. It trades at the close of the name's next bar, as
  the simulator fills (a strategy at tick T is handed the bars before T and fills at
  bar T; when the name has no bar at T the order waits for its next one).
- ``daily``: one decision per session, at its last tick. The decision reads the name's
  last close before the session's final bar, and the session's volume up to there; it
  trades at the close of the final bar. A name with no bar at the final tick has no
  decision that day. The daily series an indicator reads is these decision closes, one
  per session.

A **horizon** is where the return is measured to, from the entry:

- ``bars``: so many 5-minute bars later, inside the entry's session (a return that
  would run past the last tick is not measured). The exit is the name's last close at
  or before that time. If it has not traded since the entry the return is 0 and the
  observation is counted as stale: the simulator marks a position the same way.
- ``sessions``: the name's last bar of the session that many sessions after the
  entry's (0: the entry's own close). No bar that session, or no session that far in
  the bars loaded: not measured. This is what keeps returns out of the holdout: the
  bars stop at the last evaluation day.

Returns are total: ``(close at exit + dividends since the entry) / close at entry - 1``
in one set of share terms.

A **grid** holds one number per (session, slot, name), NaN where there is none. Only
sessions from ``first_observed`` on are in a grid (earlier ones warm the indicators up),
and only decisions whose name is in force on the universe timeline on the entry's
session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np

from .bars import BAR, SLOTS_PER_SESSION, NameBars, session_final_times
from .indicators import IndicatorInput

Scale = Literal["5min", "daily"]

# Fewer names than this with a return over the same interval: no market to compare with.
MIN_NAMES_FOR_MARKET = 5


@dataclass(frozen=True)
class Horizon:
    kind: Literal["bars", "sessions"]
    count: int

    def __post_init__(self) -> None:
        if self.kind == "bars" and self.count < 1:
            raise ValueError("a bars horizon is at least one bar")
        if self.kind == "sessions" and self.count < 0:
            raise ValueError("a sessions horizon cannot be negative")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.count}"

    @property
    def label(self) -> str:
        if self.kind == "bars":
            minutes = self.count * 5
            if minutes % 60 == 0:
                hours = minutes // 60
                return f"{hours} hour" if hours == 1 else f"{hours} hours"
            return f"{minutes} minutes"
        if self.count == 0:
            return "to the close"
        return "1 session" if self.count == 1 else f"{self.count} sessions"

    @property
    def sessions_spanned(self) -> int:
        """How many sessions after the entry's the return can reach."""
        return self.count if self.kind == "sessions" else 0


HORIZONS: dict[Scale, tuple[Horizon, ...]] = {
    "5min": (
        Horizon("bars", 1),
        Horizon("bars", 3),
        Horizon("bars", 12),
        Horizon("sessions", 0),
        Horizon("sessions", 1),
        Horizon("sessions", 5),
    ),
    "daily": (
        Horizon("sessions", 1),
        Horizon("sessions", 5),
        Horizon("sessions", 10),
    ),
}


def parse_horizon(key: str) -> Horizon:
    kind, _, count = key.partition(":")
    if kind not in ("bars", "sessions") or not count.isdigit():
        raise ValueError(f"a horizon is 'bars:N' or 'sessions:N', got {key!r}")
    return Horizon(kind, int(count))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Decisions:
    """One name's decisions on one scale, in time order."""

    symbol: str
    # What an indicator reads: one row per decision.
    indicator_input: IndicatorInput
    session: np.ndarray  # the decision's session
    slot: np.ndarray  # its slot (0 on the daily scale)
    # Index into the name's 5-minute bars of the bar it trades on; -1 when there is none.
    entry_bar: np.ndarray

    def __len__(self) -> int:
        return len(self.entry_bar)


def five_minute_decisions(bars: NameBars) -> Decisions:
    count = len(bars)
    entry = np.arange(1, count + 1, dtype=np.int64)
    if count:
        entry[-1] = -1
    return Decisions(
        symbol=bars.symbol,
        indicator_input=IndicatorInput(close=bars.close, volume=bars.volume, terms=bars.terms),
        session=bars.session,
        slot=bars.slot,
        entry_bar=entry,
    )


def daily_decisions(bars: NameBars, session_final_time: np.ndarray) -> Decisions:
    closes: list[float] = []
    volumes: list[float] = []
    terms: list[float] = []
    sessions: list[int] = []
    entries: list[int] = []
    # Bars are in time order, so each session's bars are one run.
    boundaries = np.flatnonzero(np.diff(bars.session)) + 1
    starts = np.concatenate(([0], boundaries)) if len(bars) else np.array([], dtype=np.int64)
    ends = np.concatenate((boundaries, [len(bars)])) if len(bars) else starts
    for start, end in zip(starts, ends, strict=True):
        session = int(bars.session[start])
        final = session_final_time[session]
        last = end - 1
        at_final = bars.timestamps[last] == final
        read_to = last - 1 if at_final else last  # the last bar before the final tick
        if read_to < start:
            continue  # nothing traded before the final tick: no decision
        closes.append(float(bars.close[read_to]))
        volumes.append(float(bars.volume[start : read_to + 1].sum()))
        terms.append(float(bars.terms[read_to]))
        sessions.append(session)
        entries.append(int(last) if at_final else -1)
    day_terms = np.array(terms)
    return Decisions(
        symbol=bars.symbol,
        indicator_input=IndicatorInput(
            close=np.array(closes),
            volume=np.array(volumes),
            terms=day_terms,
            traded_terms=day_terms,  # the final bar is on the decision's own day
        ),
        session=np.array(sessions, dtype=np.int64),
        slot=np.zeros(len(sessions), dtype=np.int64),
        entry_bar=np.array(entries, dtype=np.int64),
    )


def forward_returns(
    bars: NameBars,
    entry_bar: np.ndarray,
    horizon: Horizon,
    session_final_time: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """For each entry, the total return to the horizon (NaN when not measured) and
    whether the exit is stale (the name had not traded since the entry)."""
    returns = np.full(len(entry_bar), np.nan)
    stale = np.zeros(len(entry_bar), dtype=bool)
    rows = np.flatnonzero(entry_bar >= 0)
    if not len(rows):
        return returns, stale
    entry = entry_bar[rows]
    entry_session = bars.session[entry]

    if horizon.kind == "bars":
        target = bars.timestamps[entry] + horizon.count * BAR
        measured = target <= session_final_time[entry_session]
        exit_bar = np.searchsorted(bars.timestamps, target, side="right") - 1
        stale_exit = exit_bar == entry
    else:
        target_session = entry_session + horizon.count
        in_bars = target_session < len(session_final_time)
        exit_bar = np.where(
            in_bars,
            bars.session_last_bar[np.minimum(target_session, len(session_final_time) - 1)],
            -1,
        )
        measured = in_bars & (exit_bar > entry)
        stale_exit = np.zeros(len(rows), dtype=bool)

    exit_bar = np.where(measured, exit_bar, entry)  # a safe index where not measured
    price = bars.restated_close
    received = bars.dividends_paid[exit_bar] - bars.dividends_paid[entry]
    value = (price[exit_bar] + received) / price[entry] - 1.0
    returns[rows] = np.where(measured, value, np.nan)
    stale[rows] = measured & stale_exit
    return returns, stale


@dataclass(frozen=True)
class Panel:
    """Every name's decisions on one scale, and where each sits in a grid."""

    scale: Scale
    sessions: tuple[date, ...]  # every session loaded, warmup included
    first_observed: int  # index in ``sessions`` of the first session a grid holds
    symbols: tuple[str, ...]
    names: tuple[NameBars, ...]
    decisions: tuple[Decisions, ...]
    session_final_time: np.ndarray
    # Per name: which of its decisions are in a grid (observed session, a bar to trade
    # on, the name in force on the entry's session).
    counted: tuple[np.ndarray, ...]

    @property
    def slots(self) -> int:
        return SLOTS_PER_SESSION if self.scale == "5min" else 1

    @property
    def observed_sessions(self) -> tuple[date, ...]:
        return self.sessions[self.first_observed :]

    @property
    def shape(self) -> tuple[int, int, int]:
        return (len(self.sessions) - self.first_observed, self.slots, len(self.symbols))

    def grid(self, columns: Sequence[np.ndarray]) -> np.ndarray:
        """One column per name (a value per decision) laid on the grid; NaN where a
        decision is not counted or there is none."""
        if len(columns) != len(self.symbols):
            raise ValueError("one column per name is needed")
        out = np.full(self.shape, np.nan)
        for index, (decisions, counted, column) in enumerate(
            zip(self.decisions, self.counted, columns, strict=True)
        ):
            if len(column) != len(decisions):
                raise ValueError(f"{decisions.symbol}: the column is not one value per decision")
            out[
                decisions.session[counted] - self.first_observed, decisions.slot[counted], index
            ] = np.asarray(column, dtype=float)[counted]
        return out


def build_panel(
    *,
    scale: Scale,
    names: Sequence[NameBars],
    sessions: Sequence[date],
    first_observed: int,
    in_force: np.ndarray,
) -> Panel:
    """``in_force[session, name]``: whether the name is on the list in force that
    session (names in the order given)."""
    if in_force.shape != (len(sessions), len(names)):
        raise ValueError("in_force must be sessions x names")
    if not 0 <= first_observed < len(sessions):
        raise ValueError("first_observed must be one of the sessions")
    final = session_final_times(names, len(sessions))
    decisions = tuple(
        five_minute_decisions(name) if scale == "5min" else daily_decisions(name, final)
        for name in names
    )
    counted = []
    for index, (name, made) in enumerate(zip(names, decisions, strict=True)):
        has_entry = made.entry_bar >= 0
        entry_session = name.session[np.where(has_entry, made.entry_bar, 0)]
        counted.append(
            has_entry & (made.session >= first_observed) & in_force[entry_session, index]
        )
    return Panel(
        scale=scale,
        sessions=tuple(sessions),
        first_observed=first_observed,
        symbols=tuple(name.symbol for name in names),
        names=tuple(names),
        decisions=decisions,
        session_final_time=final,
        counted=tuple(counted),
    )


@dataclass(frozen=True)
class ForwardGrid:
    """The return after every counted decision, to one horizon."""

    horizon: Horizon
    raw: np.ndarray  # the name's own total return
    excess: np.ndarray  # less the mean of the names in force over the same interval
    measured: int
    stale: int  # of the measured: exits with no trade since the entry


def forward_grid(panel: Panel, horizon: Horizon) -> ForwardGrid:
    columns = []
    stale_total = 0
    for name, decisions, counted in zip(panel.names, panel.decisions, panel.counted, strict=True):
        returns, stale = forward_returns(
            name, decisions.entry_bar, horizon, panel.session_final_time
        )
        columns.append(returns)
        stale_total += int((stale & counted).sum())
    raw = panel.grid(columns)
    have = ~np.isnan(raw)
    names_with_return = have.sum(axis=2, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        market = np.where(have, raw, 0.0).sum(axis=2, keepdims=True) / names_with_return
    market = np.where(names_with_return >= MIN_NAMES_FOR_MARKET, market, np.nan)
    return ForwardGrid(
        horizon=horizon,
        raw=raw,
        excess=raw - market,
        measured=int(have.sum()),
        stale=stale_total,
    )
