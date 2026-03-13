import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class AuditLoggingService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_run_started(self, run_id: str, component: str, metadata: dict | None = None) -> None:
        self._record_event(
            run_id=run_id,
            event_type="RUN_STARTED",
            component=component,
            message="Run started",
            metadata=metadata,
        )

    def record_bar_missing(self, run_id: str, symbol: str, cycle_timestamp: datetime) -> None:
        self._record_event(
            run_id=run_id,
            event_type="BAR_MISSING",
            component="market_ingestion",
            message=f"Missing bar detected for {symbol}",
            metadata={
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp.isoformat(),
            },
        )

    def record_bar_late(self, run_id: str, symbol: str, bar_end_timestamp: datetime) -> None:
        self._record_event(
            run_id=run_id,
            event_type="BAR_LATE",
            component="market_ingestion",
            message=f"Late bar detected for {symbol}",
            metadata={
                "symbol": symbol,
                "bar_end_timestamp": bar_end_timestamp.isoformat(),
            },
        )

    def record_bar_outlier(
        self,
        run_id: str,
        symbol: str,
        cycle_timestamp: datetime,
        reference_close: Decimal,
        observed_close: Decimal,
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="BAR_OUTLIER",
            component="market_ingestion",
            message=f"Suspected outlier detected for {symbol}",
            metadata={
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp.isoformat(),
                "reference_close": str(reference_close),
                "observed_close": str(observed_close),
            },
        )

    def record_run_completed(
        self,
        run_id: str,
        component: str,
        metadata: dict | None = None,
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="RUN_COMPLETED",
            component=component,
            message="Run completed",
            metadata=metadata,
        )

    def record_run_failed(
        self,
        run_id: str,
        component: str,
        metadata: dict | None = None,
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="RUN_FAILED",
            component=component,
            message="Run failed",
            metadata=metadata,
        )

    def record_sla_breach(
        self,
        run_id: str,
        component: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="SLA_BREACH",
            component=component,
            message=message,
            metadata=metadata,
        )

    def _record_event(
        self,
        run_id: str | None,
        event_type: str,
        component: str,
        message: str,
        metadata: dict | None = None,
    ) -> None:
        event = AuditLogEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            component=component,
            event_timestamp=datetime.now(UTC),
            message=message,
            metadata=metadata,
        )

        with SorUnitOfWork(self.session) as uow:
            uow.audit_logs.add(event)
