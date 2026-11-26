"""The noisy next-session oracle: the planted control of the research pipeline.

Research plan, top board "Planted controls, M1" (decisions C1 and C2, user 2026-11-25).
Handed the run's window by the simulator (see ``planted_control.py``), it knows, for every
name and session, whether the session's last close is above its first close. At each
session's first bar it buys the names it calls up; at the session's last bar it sells.
It calls each session right with probability ``accuracy``:

- ``accuracy`` 0.60 / 0.55 / 0.52: a planted edge of known size, which the pipeline
  must find (the positive controls);
- ``accuracy`` 0.50: right half the time whatever happened, so its calls are
  independent of the outcome. That is the negative control: the same trades per day on
  the same names as the oracles, with no edge.

Why first close to last close: with the research fill model (``latency_bars`` 0) an
order fills at the close of the bar it was decided on, so the oracle buys at the first
bar's close and sells at the last bar's close, and those two prices are what it is told
about. It is flat overnight. Between the two bars it gives no signal, which the
simulator reads as "keep the position" (a BUY on every bar would re-size it each bar).

Every call is drawn from ``(seed, symbol, session date)`` alone, so a session gets the
same call in every stage and every run that contains it (selection, walk-forward folds,
stress, the holdout), and in any process.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    get_close,
)
from autonomous_trading_platform.strategy.implementations.planted_control import (
    PlantedControlNotBoundError,
    PlantedControlStrategy,
)

_SESSION_ZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class SessionCall:
    """One name's session: its first and last bar, what happened, and the oracle's call."""

    first_bar: datetime
    last_bar: datetime
    rose: bool
    called_rise: bool


def session_date(timestamp: datetime) -> date:
    """The New York trading date of a bar."""
    return timestamp.astimezone(_SESSION_ZONE).date()


def oracle_is_right(*, seed: int, symbol: str, session: date, accuracy: float) -> bool:
    """Whether the oracle calls this name's session right. A string seed is hashed with
    SHA-512 by ``random.Random``, so the draw is the same in every process."""
    draw = random.Random(f"planted_oracle|{seed}|{symbol}|{session.isoformat()}").random()
    return draw < accuracy


def session_calls(
    bars_by_symbol: dict[str, list[Any]],
    *,
    accuracy: float,
    seed: int,
    skip_timestamps: frozenset[datetime] | set[datetime] = frozenset(),
) -> dict[tuple[str, date], SessionCall]:
    """Every name's sessions in the window with the oracle's call on each.

    ``rose`` is the last bar's close above the first bar's close (an unchanged session
    counts as not risen). A session with one bar cannot be bought and sold, so it is left
    out. Bars at ``skip_timestamps`` (the window's warm-up) are not read.
    """
    calls: dict[tuple[str, date], SessionCall] = {}
    for symbol, bars in bars_by_symbol.items():
        by_session: dict[date, list[Any]] = {}
        for bar in bars:
            if bar.timestamp in skip_timestamps:
                continue
            by_session.setdefault(session_date(bar.timestamp), []).append(bar)
        for day, day_bars in by_session.items():
            if len(day_bars) < 2:
                continue
            first = min(day_bars, key=lambda b: b.timestamp)
            last = max(day_bars, key=lambda b: b.timestamp)
            rose = get_close(last) > get_close(first)
            right = oracle_is_right(seed=seed, symbol=symbol, session=day, accuracy=accuracy)
            calls[(symbol, day)] = SessionCall(
                first_bar=first.timestamp,
                last_bar=last.timestamp,
                rose=rose,
                called_rise=rose if right else not rose,
            )
    return calls


class PlantedOracleStrategy(PlantedControlStrategy):
    """Buys at a session's first bar the names it calls up; sells at its last bar."""

    def __init__(self, strategy_id: str, accuracy: float = 0.5, seed: int = 0) -> None:
        if not (0.0 <= accuracy <= 1.0):
            raise ValueError("accuracy must be in [0, 1]")
        self._strategy_id = strategy_id
        self.accuracy = accuracy
        self.seed = seed
        self._calls: dict[tuple[str, date], SessionCall] | None = None

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def bind_run_window(self, window: Any) -> None:
        self._calls = session_calls(
            window.bars_by_symbol,
            accuracy=self.accuracy,
            seed=self.seed,
            skip_timestamps=getattr(window, "warmup_timestamps", None) or frozenset(),
        )

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        if self._calls is None:
            raise PlantedControlNotBoundError(
                f"planted control {self._strategy_id!r} was asked for a signal without its "
                "window; controls run only inside the research simulator"
            )
        call = self._calls.get((context.symbol, session_date(context.bar_timestamp)))
        if call is None:
            return None

        if context.bar_timestamp == call.first_bar:
            if not call.called_rise:
                return None
            direction, reason = SignalDirection.BUY, "called_rise"
        elif context.bar_timestamp == call.last_bar:
            direction, reason = SignalDirection.SELL, "session_end"
        else:
            return None

        params = {
            "strategy_type": "planted_oracle",
            "accuracy": self.accuracy,
            "seed": self.seed,
            "reason": reason,
        }
        return Signal(
            signal_id=build_signal_id(
                run_id=context.run_id,
                strategy_id=context.strategy_id,
                symbol=context.symbol,
                bar_timestamp=context.bar_timestamp,
                direction=direction,
                params=params,
            ),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            direction=direction,
            confidence=1.0,
            params=params,
        )
