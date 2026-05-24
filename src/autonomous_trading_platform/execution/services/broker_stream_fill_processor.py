from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.execution.services.order_reconciliation_service import (
    ReconciliationInput,
)
from autonomous_trading_platform.execution.services.order_state_machine_service import (
    OrderStateMachineService,
)
from autonomous_trading_platform.observability.logging import get_logger

logger = get_logger(__name__)

UPDATE_SOURCE_STREAM = "stream"
UPDATE_SOURCE_POLL = "poll"


@dataclass(frozen=True)
class StreamProcessingResult:
    event_type: str
    broker_order_id: str
    order_found: bool
    fill_extracted: bool
    status_transitioned: bool
    new_status: OrderStatus | None
    fill: Fill | None
    skipped: bool = False
    skip_reason: str | None = None


class BrokerStreamFillProcessor:
    """
    Processes individual broker order stream events with idempotent fill extraction.

    Uses the same BrokerOrderMapper delta-qty mechanism as polling reconciliation,
    so a fill arriving via both the stream and a poll cycle cannot be double-counted.

    Callers supply the local tracking state (ReconciliationInput) for the order.
    Callers are responsible for persisting the resulting fill and status update.
    """

    def __init__(
        self,
        *,
        broker_order_mapper: BrokerOrderMapper,
        order_state_machine_service: OrderStateMachineService,
    ) -> None:
        self.broker_order_mapper = broker_order_mapper
        self.order_state_machine_service = order_state_machine_service

    def process_event(
        self,
        event_payload: dict[str, Any],
        *,
        tracked_order: ReconciliationInput | None,
        stream_received_at: datetime | None = None,
    ) -> StreamProcessingResult:
        """
        Process a single order update event from the websocket stream.

        Args:
            event_payload: Normalized event dict from AlpacaOrderStreamClient.
            tracked_order: Local tracking state. None when order is not locally tracked.
            stream_received_at: When the event was received; defaults to now.
        """
        received_at = stream_received_at or datetime.now(UTC)
        event_type = str(event_payload.get("event", "unknown"))
        order_payload = event_payload.get("order") or {}
        broker_order_id = str(order_payload.get("id", ""))

        if not broker_order_id:
            logger.warning(
                "stream.event_missing_broker_order_id",
                extra={"event_type": event_type},
            )
            return StreamProcessingResult(
                event_type=event_type,
                broker_order_id="",
                order_found=False,
                fill_extracted=False,
                status_transitioned=False,
                new_status=None,
                fill=None,
                skipped=True,
                skip_reason="missing_broker_order_id",
            )

        if tracked_order is None:
            logger.debug(
                "stream.order_not_tracked_locally",
                extra={"broker_order_id": broker_order_id, "event_type": event_type},
            )
            return StreamProcessingResult(
                event_type=event_type,
                broker_order_id=broker_order_id,
                order_found=False,
                fill_extracted=False,
                status_transitioned=False,
                new_status=None,
                fill=None,
                skipped=True,
                skip_reason="order_not_tracked",
            )

        enriched_payload = {
            **order_payload,
            "ratp_update_source": UPDATE_SOURCE_STREAM,
            "ratp_stream_received_at": received_at.isoformat(),
        }

        broker_order = self.broker_order_mapper.to_broker_order(
            payload=enriched_payload,
            intent_id=tracked_order.intent_id,
            run_id=tracked_order.run_id,
            account_id=tracked_order.account_id,
        )

        fill_result = self.broker_order_mapper.extract_incremental_fill(
            broker_order=broker_order,
            previous_filled_qty=tracked_order.previous_filled_qty,
            previous_avg_fill_price=tracked_order.previous_avg_fill_price,
            received_at=received_at,
        )

        # Tag fill with stream source for traceability.
        fill = fill_result.fill
        if fill is not None:
            existing_meta = fill.metadata or {}
            fill = fill.model_copy(
                update={
                    "metadata": {
                        **existing_meta,
                        "update_source": UPDATE_SOURCE_STREAM,
                        "stream_received_at": received_at.isoformat(),
                    }
                }
            )

        event = self.broker_order_mapper.to_order_event(broker_order)

        next_status = tracked_order.current_status
        status_transitioned = False
        if event is not None and broker_order.status != tracked_order.current_status:
            next_status = self.order_state_machine_service.apply_event(
                order_id=tracked_order.order_id,
                current_status=tracked_order.current_status,
                event=event,
                event_timestamp=received_at,
                run_id=str(tracked_order.run_id),
                metadata={
                    "broker_order_id": broker_order_id,
                    "broker_status": broker_order.status.value,
                    "event_type": event_type,
                    "update_source": UPDATE_SOURCE_STREAM,
                },
            )
            status_transitioned = True

        logger.info(
            "stream.event_processed",
            extra={
                "broker_order_id": broker_order_id,
                "event_type": event_type,
                "fill_extracted": fill is not None,
                "status_transitioned": status_transitioned,
                "new_status": next_status.value if next_status else None,
                "update_source": UPDATE_SOURCE_STREAM,
            },
        )

        return StreamProcessingResult(
            event_type=event_type,
            broker_order_id=broker_order_id,
            order_found=True,
            fill_extracted=fill is not None,
            status_transitioned=status_transitioned,
            new_status=next_status,
            fill=fill,
        )
