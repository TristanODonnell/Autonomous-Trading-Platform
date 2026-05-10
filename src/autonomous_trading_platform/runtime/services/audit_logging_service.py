import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class AuditLoggingService:
    _SENSITIVE_KEYS = {
        "api_key",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "secret",
        "client_secret",
        "authorization",
    }

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

    def record_event(
        self,
        *,
        run_id: str | None,
        event_type: str,
        component: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type=event_type,
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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        sanitized_metadata = self._sanitize_metadata(metadata)

        event = AuditLogEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            component=component,
            event_timestamp=datetime.now(UTC),
            message=message,
            metadata=sanitized_metadata,
        )

        with SorUnitOfWork(self.session) as uow:
            uow.audit_logs.add(event)

    @classmethod
    def _sanitize_metadata(cls, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        if metadata is None:
            return None

        return {key: cls._sanitize_value(key, value) for key, value in metadata.items()}

    @classmethod
    def _sanitize_value(cls, key: str, value: Any) -> Any:
        if key.lower() in cls._SENSITIVE_KEYS:
            return "[REDACTED]"

        if isinstance(value, Mapping):
            return {
                nested_key: cls._sanitize_value(str(nested_key), nested_value)
                for nested_key, nested_value in value.items()
            }

        if isinstance(value, list):
            return [cls._sanitize_value(key, item) for item in value]

        if isinstance(value, tuple):
            return tuple(cls._sanitize_value(key, item) for item in value)

        return value

    # ============================================================
    # MARKET BAR AUDITING FUNCTIONS
    # ============================================================
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

    # ============================================================
    # CORPORATE ACTION AUDITING FUNCTIONS
    # ============================================================

    def record_corporate_action_parse_failed(
        self, run_id: str, symbol: str, cycle_timestamp: datetime
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="PARSE_FAILED",
            component="corporate_action_ingestion",
            message=f"parse failed for {symbol}",
            metadata={
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp.isoformat(),
            },
        )

    def record_corporate_action_validation_failed(
        self, run_id: str, symbol: str, cycle_timestamp: datetime
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="CORPORATE_ACTION_VALIDATION_FAILED",
            component="corporate_action_ingestion",
            message=f"corporate action validation failed for {symbol}",
            metadata={
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp.isoformat(),
            },
        )

    def record_corporate_action_adjustment_applied(
        self, run_id: str, symbol: str, cycle_timestamp: datetime
    ) -> None:
        self._record_event(
            run_id=run_id,
            event_type="CORPORATE_ACTION_ADJUSTMENT_APPLIED",
            component="corporate_action_ingestion",
            message=f"corporate action adjustment applied for {symbol}",
            metadata={
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp.isoformat(),
            },
        )
