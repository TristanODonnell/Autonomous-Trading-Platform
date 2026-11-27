"""One name's 5-minute bars as the screen reads them.

Built once per name from its bars and corporate actions. Everything the screen does
later (indicators, entries, exits, returns) is arithmetic on these arrays.

- **Session and slot.** A bar belongs to the session of its UTC date, as the simulator
  and buy and hold date it, and to a 5-minute slot counted from 09:30 New York time
  (slot 0) to 15:55 (slot 77), so the same slot is the same time of day in winter and
  summer. A bar on a day that is not one of the screen's sessions, or outside those
  slots, is not read.
- **Terms.** The price factor restating each bar in the share terms of the last bar
  loaded (``accounting.corporate_actions.split_factor_for`` with ``as_of`` the last
  day): a continuous price series across splits.
- **Dividends paid.** Cash per share, in the same terms, with an ex-date on or before
  the bar's day, summed. A holder from bar ``i`` to bar ``j`` received
  ``dividends_paid[j] - dividends_paid[i]``: the ex-dates after the entry's day up to
  the exit's day, the rule buy and hold uses (``benchmark_report.benchmark_series``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from autonomous_trading_platform.accounting.corporate_actions import (
    dividend_per_share_of,
    is_applicable,
    is_cash_dividend,
    is_split,
    split_ratio_of,
)

SLOTS_PER_SESSION = 78
BAR = np.timedelta64(5, "m")
_NEW_YORK = "America/New_York"
_OPEN_MINUTE = 9 * 60 + 30


@dataclass(frozen=True)
class NameBars:
    symbol: str
    timestamps: np.ndarray  # datetime64[ns], UTC, strictly increasing
    close: np.ndarray  # as traded
    volume: np.ndarray  # as traded
    terms: np.ndarray
    dividends_paid: np.ndarray
    session: np.ndarray  # index into the screen's sessions
    slot: np.ndarray  # 0 .. SLOTS_PER_SESSION - 1
    # For each of the screen's sessions, the index of this name's last bar in it; -1
    # when it has none.
    session_last_bar: np.ndarray
    # Bars handed in and not read: off-session days, outside the slots, repeats.
    bars_not_read: int

    @property
    def restated_close(self) -> np.ndarray:
        return self.close * self.terms

    def __len__(self) -> int:
        return len(self.timestamps)


def build_name_bars(
    *,
    symbol: str,
    timestamps: Sequence[Any],
    close: Sequence[float],
    volume: Sequence[float],
    actions: Sequence[Any],
    sessions: Sequence[date],
) -> NameBars:
    """``timestamps`` may be in any order and any timezone-aware form; ``actions`` are
    this name's corporate actions (others are ignored); ``sessions`` the screen's
    sessions in order."""
    if not isinstance(timestamps, pd.Series | pd.Index | np.ndarray):
        timestamps = list(timestamps)
    stamps = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
    frame = pd.DataFrame(
        {
            "close": np.asarray(close, dtype=float),
            "volume": np.asarray(volume, dtype=float),
        },
        index=stamps,
    )
    handed = len(frame)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()

    local = frame.index.tz_convert(_NEW_YORK)
    minute = local.hour * 60 + local.minute - _OPEN_MINUTE
    slot = minute // 5
    session_of = {day: index for index, day in enumerate(sessions)}
    session = np.array([session_of.get(day, -1) for day in frame.index.date], dtype=np.int64)
    read = (session >= 0) & (minute % 5 == 0) & (slot >= 0) & (slot < SLOTS_PER_SESSION)

    frame = frame[read]
    session = session[read]
    days = np.array(frame.index.date)
    mine = [a for a in actions if a.symbol.upper() == symbol.upper() and is_applicable(a)]
    splits = sorted((a for a in mine if is_split(a)), key=lambda a: a.effective_date)

    terms = np.ones(len(frame))
    for split in splits:
        terms[days < split.effective_date] /= float(split_ratio_of(split))

    dividends_paid = np.zeros(len(frame))
    for action in mine:
        if not is_cash_dividend(action):
            continue
        per_share = float(dividend_per_share_of(action))
        for split in splits:  # restate the dividend as the bars before later splits are
            if split.effective_date > action.effective_date:
                per_share /= float(split_ratio_of(split))
        dividends_paid[days >= action.effective_date] += per_share

    session_last_bar = np.full(len(sessions), -1, dtype=np.int64)
    session_last_bar[session] = np.arange(len(frame))  # in time order: the last write wins

    return NameBars(
        symbol=symbol,
        timestamps=frame.index.tz_localize(None).to_numpy(dtype="datetime64[ns]"),
        close=frame["close"].to_numpy(),
        volume=frame["volume"].to_numpy(),
        terms=terms,
        dividends_paid=dividends_paid,
        session=session,
        slot=np.asarray(slot[read], dtype=np.int64),
        session_last_bar=session_last_bar,
        bars_not_read=handed - len(frame),
    )


def session_final_times(names: Sequence[NameBars], sessions: int) -> np.ndarray:
    """For each session, the time of its last bar across every name: the last tick of
    the day (15:55 New York; 12:55 on an early close). NaT for a session no name has a
    bar in."""
    final = np.full(sessions, np.datetime64("NaT"), dtype="datetime64[ns]")
    for name in names:
        has = name.session_last_bar >= 0
        last = name.timestamps[name.session_last_bar[has]]
        current = final[has]
        final[has] = np.where(np.isnat(current) | (last > current), last, current)
    return final
