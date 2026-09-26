"""
Delisting detection from bar gaps (historical replay).

A symbol that had bars and then shows none for ``grace_trading_days``
consecutive market days — days on which other symbols did trade — is treated
as delisted, effective the day after its last bar. The event is recorded as a
TickerLifecycle DELISTING, which:

  - drops the symbol from the trading universe (resolve_trading_universe), and
  - lets SimulatedBrokerClient price it at its last close, so the normal
    exit-delta path sells any open position instead of holding it forever at a
    stale price.

Limitations
-----------
- Exiting at the last exchange close is optimistic for bankruptcies (e.g. SIVB
  was halted and later traded OTC far lower); for acquisitions it is close to
  the cash-out price.
- A ticker rename also stops bars under the old symbol, so it is treated as a
  delisting: the position is exited at a fair last price rather than carried
  over to the new ticker.
- Detection lags the real delisting by grace_trading_days.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEvent,
    TickerLifecycleEventType,
)

DEFAULT_GRACE_TRADING_DAYS = 5
_SOURCE = "replay_bar_gap_detection"


@dataclass(frozen=True)
class DetectedDelisting:
    symbol: str
    last_bar_date: date
    missing_market_days: int

    @property
    def effective_at(self) -> datetime:
        return datetime.combine(self.last_bar_date + timedelta(days=1), time.min, tzinfo=UTC)


def find_delisted_symbols(
    *,
    last_bar_dates: Mapping[str, date],
    market_dates: Sequence[date],
    today: date,
    grace_trading_days: int = DEFAULT_GRACE_TRADING_DAYS,
) -> list[DetectedDelisting]:
    """Symbols whose bars stopped at least grace_trading_days market days ago.

    last_bar_dates   last date each symbol had a bar (symbols never seen are
                     absent — a future listing is not a delisting)
    market_dates     dates on which any symbol traded (market-open days)
    """
    if grace_trading_days < 1:
        raise ValueError("grace_trading_days must be >= 1")
    open_days = sorted(d for d in set(market_dates) if d <= today)
    detected: list[DetectedDelisting] = []
    for symbol, last in sorted(last_bar_dates.items()):
        missing = sum(1 for d in open_days if d > last)
        if missing >= grace_trading_days:
            detected.append(
                DetectedDelisting(symbol=symbol, last_bar_date=last, missing_market_days=missing)
            )
    return detected


class DelistingDetectionService:
    def __init__(
        self,
        *,
        coverage_repository,  # SymbolDateCoverageRepository
        lifecycle_repository,  # TickerLifecycleRepository
        grace_trading_days: int = DEFAULT_GRACE_TRADING_DAYS,
    ) -> None:
        self._coverage = coverage_repository
        self._lifecycle = lifecycle_repository
        self._grace = grace_trading_days

    def detect_and_record(
        self,
        *,
        dataset_version: str,
        symbols: Iterable[str],
        today: date,
    ) -> list[DetectedDelisting]:
        """Record DELISTING events for newly detected symbols; return only new ones.

        Idempotent: the event id is derived from (symbol, last bar date), and
        symbols already delisted as of today are skipped.
        """
        last_bar_dates = self._coverage.last_dates_with_bars(
            dataset_version=dataset_version, symbols=symbols
        )
        if not last_bar_dates:
            return []
        market_dates = self._coverage.dates_with_any_bars(
            dataset_version=dataset_version, since=min(last_bar_dates.values())
        )
        today_dt = datetime.combine(today, time.max, tzinfo=UTC)

        recorded: list[DetectedDelisting] = []
        for found in find_delisted_symbols(
            last_bar_dates=last_bar_dates,
            market_dates=market_dates,
            today=today,
            grace_trading_days=self._grace,
        ):
            latest = self._lifecycle.get_latest_event_for_symbol_as_of(found.symbol, today_dt)
            if latest is not None and latest.event_type == TickerLifecycleEventType.DELISTING:
                continue
            self._lifecycle.upsert(
                TickerLifecycleEvent(
                    event_id=f"delisting:{found.symbol}:{found.last_bar_date.isoformat()}",
                    symbol=found.symbol,
                    event_type=TickerLifecycleEventType.DELISTING,
                    effective_at=found.effective_at,
                    source=_SOURCE,
                    notes=(
                        f"No bars for {found.missing_market_days} market days after "
                        f"{found.last_bar_date.isoformat()} in {dataset_version}"
                    ),
                )
            )
            recorded.append(found)
        return recorded
