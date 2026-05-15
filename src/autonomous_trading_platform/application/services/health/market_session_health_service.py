from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from autonomous_trading_platform.contracts.runtime.detailed_health import (
    HealthCheckName,
    HealthCheckResult,
    HealthSeverity,
    HealthStatus,
    ServiceHealthReport,
)
from autonomous_trading_platform.runtime.clock import (
    MarketCalendar,
    MarketPhase,
    RealMarketCalendar,
    RealTradingClock,
    TradingClock,
)

logger = logging.getLogger(__name__)


def _ok(name: HealthCheckName, message: str, metadata: dict[str, Any]) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.OK,
        severity=HealthSeverity.INFO,
        message=message,
        metadata=metadata,
    )


def _degraded(
    name: HealthCheckName,
    message: str,
    metadata: dict[str, Any],
) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.DEGRADED,
        severity=HealthSeverity.WARNING,
        message=message,
        metadata=metadata,
    )


def _to_iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class MarketSessionHealthService:
    """Reports the calendar/session state that runtime decisions depend on."""

    def __init__(
        self,
        *,
        clock: TradingClock | None = None,
        calendar: MarketCalendar | None = None,
    ) -> None:
        self._clock = clock or RealTradingClock()
        self._calendar = calendar or RealMarketCalendar()

    def check(self) -> ServiceHealthReport:
        now = self._clock.now()
        state = self._calendar.session_state(now)
        metadata = {
            "checked_at": now.astimezone(UTC).isoformat(),
            "current_market_phase": state.current_market_phase.value,
            "next_market_open": _to_iso(state.next_market_open),
            "next_market_close": _to_iso(state.next_market_close),
            "current_trading_date": _to_iso(state.current_trading_date),
            "eod_eligible": state.eod_eligible,
            "calendar_source": state.calendar_source,
            "is_trading_day": state.is_trading_day,
            "is_early_close": state.is_early_close,
            "closure_reason": state.closure_reason,
        }
        _log_session_state(metadata)

        if state.current_market_phase == MarketPhase.NON_TRADING_DAY:
            check = _ok(
                HealthCheckName.MARKET_SESSION_STATE,
                "Market is closed for a non-trading day.",
                metadata,
            )
        elif state.current_market_phase == MarketPhase.PRE_MARKET:
            check = _ok(
                HealthCheckName.MARKET_SESSION_STATE,
                "Market is in the premarket window.",
                metadata,
            )
        elif state.current_market_phase == MarketPhase.MARKET_HOURS:
            check = _ok(
                HealthCheckName.MARKET_SESSION_STATE,
                "Market is open.",
                metadata,
            )
        elif state.current_market_phase == MarketPhase.POST_MARKET:
            check = _ok(
                HealthCheckName.MARKET_SESSION_STATE,
                "Market is in the postmarket window.",
                metadata,
            )
        else:
            check = _degraded(
                HealthCheckName.MARKET_SESSION_STATE,
                "Runtime detected an invalid trading window.",
                metadata,
            )

        return ServiceHealthReport(service="market_session", status=check.status, checks=[check])


def _log_session_state(metadata: dict[str, Any]) -> None:
    phase = metadata["current_market_phase"]
    if phase == MarketPhase.MARKET_HOURS.value:
        logger.info("market_session.open", extra=metadata)
    elif phase == MarketPhase.POST_MARKET.value:
        logger.info("market_session.close", extra=metadata)
    elif phase == MarketPhase.NON_TRADING_DAY.value:
        event = (
            "market_session.holiday_closure"
            if metadata.get("closure_reason") == "holiday"
            else "market_session.invalid_trading_window"
        )
        logger.info(event, extra=metadata)

    if metadata.get("is_early_close"):
        logger.info("market_session.early_close", extra=metadata)
    if metadata.get("eod_eligible"):
        logger.info("market_session.eod_window_open", extra=metadata)
