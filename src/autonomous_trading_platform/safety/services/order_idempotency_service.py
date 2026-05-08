from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.safety.errors import DuplicateIdempotencyKeyError
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)


class OrderIdempotencyService:
    def __init__(
        self,
        settings: Settings,
        order_activity_reader,
        audit_log_repository: AuditLogRepository,
    ) -> None:
        self.settings = settings
        self.order_activity_reader = order_activity_reader
        self.audit_log_repository = audit_log_repository

    def build_idempotency_key(self, order_intent: OrderIntent) -> str:
        if order_intent.qty is None:
            raise ValueError("Order intent qty must be set for idempotency key generation.")

        raw_key = "|".join(
            [
                str(order_intent.run_id),
                order_intent.strategy_id,
                order_intent.bar_timestamp.astimezone(UTC).isoformat(),
                order_intent.symbol.upper(),
                order_intent.side.value
                if hasattr(order_intent.side, "value")
                else str(order_intent.side),
                str(order_intent.qty),
            ]
        )

        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def assert_not_duplicate_within_window(
        self,
        order_intent: OrderIntent,
        now: datetime,
    ) -> str:
        idempotency_key = self.build_idempotency_key(order_intent)

        window_minutes = self.settings.idempotency_deduplication_window_minutes
        window_start = now - timedelta(minutes=window_minutes)

        duplicate_exists = self.order_activity_reader.idempotency_key_exists_between(
            idempotency_key=idempotency_key,
            start_time=window_start,
            end_time=now,
        )

        if duplicate_exists:
            self.audit_log_repository.add(
                AuditLogEvent(
                    event_id=str(uuid4()),
                    run_id=str(order_intent.run_id),
                    event_type="ORDER_IDEMPOTENCY_DUPLICATE_DETECTED",
                    component="safety.order_idempotency",
                    event_timestamp=now,
                    message="Duplicate order intent detected within deduplication window.",
                    metadata={
                        "idempotency_key": idempotency_key,
                        "strategy_id": order_intent.strategy_id,
                        "symbol": order_intent.symbol,
                        "side": (
                            order_intent.side.value
                            if hasattr(order_intent.side, "value")
                            else str(order_intent.side)
                        ),
                        "qty": str(order_intent.qty),
                        "bar_timestamp": order_intent.bar_timestamp.astimezone(UTC).isoformat(),
                        "window_start": window_start.astimezone(UTC).isoformat(),
                        "checked_at": now.astimezone(UTC).isoformat(),
                    },
                )
            )
            raise DuplicateIdempotencyKeyError(
                f"Duplicate order intent detected within deduplication window: {idempotency_key}"
            )

        self.audit_log_repository.add(
            AuditLogEvent(
                event_id=str(uuid4()),
                run_id=str(order_intent.run_id),
                event_type="ORDER_IDEMPOTENCY_CHECK_PASSED",
                component="safety.order_idempotency",
                event_timestamp=now,
                message="Order intent passed idempotency duplicate check.",
                metadata={
                    "idempotency_key": idempotency_key,
                    "strategy_id": order_intent.strategy_id,
                    "symbol": order_intent.symbol,
                    "side": (
                        order_intent.side.value
                        if hasattr(order_intent.side, "value")
                        else str(order_intent.side)
                    ),
                    "qty": str(order_intent.qty),
                    "bar_timestamp": order_intent.bar_timestamp.astimezone(UTC).isoformat(),
                    "window_start": window_start.astimezone(UTC).isoformat(),
                    "checked_at": now.astimezone(UTC).isoformat(),
                },
            )
        )

        return idempotency_key
