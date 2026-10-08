"""Daily cross-check of the local market calendar against the broker's.

``RealMarketCalendar`` takes sessions, holidays and early closes from ``exchange_calendars``,
which is only as current as the installed package. Alpaca publishes the same schedule
(``GET /v2/calendar``). Comparing the next couple of weeks catches a stale package or an
unscheduled closure before the scheduler trades or skips a day on wrong information.
The local calendar stays the source of truth; a mismatch is reported, not applied.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.runtime.clock import MarketCalendar

logger = get_logger(__name__)

_ET = ZoneInfo("America/New_York")
BrokerSessions = Mapping[date, tuple[time, time]]  # ET open and close per session date


@dataclass(frozen=True)
class CalendarMismatch:
    day: date
    kind: str  # session_only_local | session_only_broker | open_time | close_time
    local: str | None
    broker: str | None


def cross_check_market_calendar(
    calendar: MarketCalendar,
    broker_sessions: BrokerSessions,
    *,
    start: date,
    end: date,
) -> list[CalendarMismatch]:
    """Compare which days are sessions, and their open/close times, over [start, end]."""
    mismatches: list[CalendarMismatch] = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        local_session = calendar.is_trading_day(day)
        broker = broker_sessions.get(day)
        if local_session and broker is None:
            mismatches.append(CalendarMismatch(day, "session_only_local", "session", None))
            continue
        if not local_session and broker is not None:
            mismatches.append(CalendarMismatch(day, "session_only_broker", None, "session"))
            continue
        if not local_session or broker is None:
            continue
        local_open = calendar.market_open(day).astimezone(_ET).time()
        local_close = calendar.market_close(day).astimezone(_ET).time()
        broker_open, broker_close = broker
        if local_open != broker_open:
            mismatches.append(
                CalendarMismatch(day, "open_time", local_open.isoformat(), broker_open.isoformat())
            )
        if local_close != broker_close:
            mismatches.append(
                CalendarMismatch(
                    day, "close_time", local_close.isoformat(), broker_close.isoformat()
                )
            )
    return mismatches


def fetch_alpaca_calendar(settings: Settings, *, start: date, end: date) -> BrokerSessions:
    """Alpaca's trading calendar for [start, end]; times are ET as the API returns them."""
    response = httpx.get(
        f"{settings.alpaca_base_url}/v2/calendar",
        params={"start": start.isoformat(), "end": end.isoformat()},
        headers={
            "APCA-API-KEY-ID": settings.broker_api_key or "",
            "APCA-API-SECRET-KEY": settings.broker_api_secret or "",
        },
        timeout=15.0,
    )
    response.raise_for_status()
    sessions: dict[date, tuple[time, time]] = {}
    for row in response.json():
        sessions[date.fromisoformat(row["date"])] = (
            time.fromisoformat(row["open"]),
            time.fromisoformat(row["close"]),
        )
    return sessions


def run_calendar_cross_check(
    calendar: MarketCalendar,
    *,
    now_utc: datetime,
    horizon_days: int = 14,
    broker_sessions: BrokerSessions | None = None,
) -> dict[str, object]:
    """Check the next ``horizon_days`` and log every mismatch. Returns a job summary."""
    start = now_utc.astimezone(_ET).date() + timedelta(days=1)
    end = start + timedelta(days=horizon_days - 1)
    if broker_sessions is None:
        broker_sessions = fetch_alpaca_calendar(Settings(), start=start, end=end)
    mismatches = cross_check_market_calendar(calendar, broker_sessions, start=start, end=end)
    for m in mismatches:
        logger.warning(
            "market_calendar.mismatch",
            extra={"day": m.day.isoformat(), "kind": m.kind, "local": m.local, "broker": m.broker},
        )
    return {
        "checked_from": start.isoformat(),
        "checked_to": end.isoformat(),
        "broker_sessions": len(broker_sessions),
        "mismatches": [
            {"day": m.day.isoformat(), "kind": m.kind, "local": m.local, "broker": m.broker}
            for m in mismatches
        ],
    }
