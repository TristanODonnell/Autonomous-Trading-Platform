from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from autonomous_trading_platform.contracts.common.enums import (
    LiquiditySide,
    OrderEvent,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.storage.sor.models.broker_orders import (
    BrokerOrder as BrokerOrderRow,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill as FillRow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillExtractionResult:
    fill: Fill | None
    new_filled_qty: Decimal


class BrokerOrderMapper:
    def to_broker_order(
        self,
        *,
        payload: dict[str, Any],
        intent_id: UUID,
        run_id: UUID,
        account_id: str,
    ) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=str(payload["id"]),
            client_order_id=str(payload.get("client_order_id", "")),
            intent_id=intent_id,
            run_id=run_id,
            broker="alpaca",
            account_id=account_id,
            symbol=str(payload["symbol"]),
            side=self._parse_side(str(payload["side"])),
            order_type=self._parse_order_type(str(payload["type"])),
            time_in_force=self._parse_time_in_force(str(payload["time_in_force"])),
            extended_hours=bool(payload.get("extended_hours", False)),
            qty=self._to_decimal_or_none(payload.get("qty")),
            notional=self._to_decimal_or_none(payload.get("notional")),
            limit_price=self._to_decimal_or_none(payload.get("limit_price")),
            stop_price=self._to_decimal_or_none(payload.get("stop_price")),
            status=self._map_order_status(str(payload.get("status", ""))),
            submitted_at=self._parse_datetime(payload.get("submitted_at")),
            updated_at=(
                self._parse_datetime(payload.get("updated_at"))
                or self._parse_datetime(payload.get("filled_at"))
                or datetime.now(UTC)
            ),
            filled_qty=self._to_decimal(payload.get("filled_qty")),
            avg_fill_price=self._to_decimal_or_none(payload.get("filled_avg_price")),
            last_error=self._extract_last_error(payload),
            raw_broker_payload=payload,
            requested_qty=self._to_decimal_or_none(payload.get("qty")),
            signal_generated_at=self._parse_datetime(payload.get("ratp_signal_generated_at")),
            submitted_to_broker_at=self._parse_datetime(payload.get("ratp_submitted_to_broker_at")),
            broker_acknowledged_at=(
                self._parse_datetime(payload.get("ratp_broker_acknowledged_at"))
                or self._parse_datetime(payload.get("accepted_at"))
                or self._parse_datetime(payload.get("submitted_at"))
                or datetime.now(UTC)
            ),
            first_fill_at=self._parse_datetime(payload.get("filled_at")),
        )

    @staticmethod
    def to_fill_orm_row(fill: Fill) -> FillRow:
        return FillRow(
            fill_id=fill.fill_id,
            broker_order_id=fill.broker_order_id,
            intent_id=fill.intent_id,
            run_id=fill.run_id,
            timestamp=fill.timestamp,
            symbol=fill.symbol,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            fees=fill.fees,
            liquidity=fill.liquidity,
            venue=fill.venue,
            meta=fill.metadata,
        )

    @staticmethod
    def to_orm_row(broker_order: BrokerOrder) -> BrokerOrderRow:
        return BrokerOrderRow(
            broker_order_id=broker_order.broker_order_id,
            client_order_id=broker_order.client_order_id,
            intent_id=broker_order.intent_id,
            run_id=broker_order.run_id,
            broker=broker_order.broker,
            account_id=broker_order.account_id,
            symbol=broker_order.symbol,
            side=broker_order.side,
            order_type=broker_order.order_type,
            time_in_force=broker_order.time_in_force,
            extended_hours=broker_order.extended_hours,
            qty=broker_order.qty,
            notional=broker_order.notional,
            limit_price=broker_order.limit_price,
            stop_price=broker_order.stop_price,
            status=broker_order.status,
            submitted_at=broker_order.submitted_at,
            updated_at=broker_order.updated_at,
            filled_qty=broker_order.filled_qty,
            avg_fill_price=broker_order.avg_fill_price,
            last_error=broker_order.last_error,
            raw_broker_payload=broker_order.raw_broker_payload,
            requested_qty=broker_order.requested_qty,
            signal_generated_at=broker_order.signal_generated_at,
            submitted_to_broker_at=broker_order.submitted_to_broker_at,
            broker_acknowledged_at=broker_order.broker_acknowledged_at,
            first_fill_at=broker_order.first_fill_at,
        )

    def to_order_event(self, broker_order: BrokerOrder) -> OrderEvent | None:
        if broker_order.status == OrderStatus.SUBMITTED:
            return None
        if broker_order.status == OrderStatus.PARTIALLY_FILLED:
            return OrderEvent.PARTIAL_FILL
        if broker_order.status == OrderStatus.FILLED:
            return OrderEvent.FULL_FILL
        if broker_order.status == OrderStatus.CANCELED:
            return OrderEvent.CANCEL
        if broker_order.status == OrderStatus.REJECTED:
            return OrderEvent.REJECT
        return None

    def extract_incremental_fill(
        self,
        *,
        broker_order: BrokerOrder,
        previous_filled_qty: Decimal,
        previous_avg_fill_price: Decimal | None,
        received_at: datetime | None = None,
    ) -> FillExtractionResult:
        current_filled_qty = Decimal(str(broker_order.filled_qty))
        delta_qty = current_filled_qty - previous_filled_qty

        if delta_qty < Decimal("0"):
            # Stale or out-of-order broker update. Preserve monotonic state by
            # returning previous_filled_qty so callers do not rewind tracked qty.
            logger.critical(
                "fill.quantity_regression_detected",
                extra={
                    "broker_order_id": broker_order.broker_order_id,
                    "intent_id": str(broker_order.intent_id),
                    "symbol": broker_order.symbol,
                    "previous_filled_qty": str(previous_filled_qty),
                    "current_filled_qty": str(current_filled_qty),
                    "delta_qty": str(delta_qty),
                    "broker_status": broker_order.status.value,
                    "received_at": received_at.isoformat() if received_at is not None else None,
                    "broker_update_timestamp": broker_order.updated_at.isoformat(),
                },
            )
            return FillExtractionResult(
                fill=None,
                new_filled_qty=previous_filled_qty,
            )

        if delta_qty == Decimal("0"):
            logger.debug(
                "fill.duplicate_broker_update",
                extra={
                    "broker_order_id": broker_order.broker_order_id,
                    "intent_id": str(broker_order.intent_id),
                    "filled_qty": str(current_filled_qty),
                    "received_at": received_at.isoformat() if received_at is not None else None,
                },
            )
            return FillExtractionResult(
                fill=None,
                new_filled_qty=current_filled_qty,
            )

        if broker_order.avg_fill_price is None:
            return FillExtractionResult(
                fill=None,
                new_filled_qty=current_filled_qty,
            )

        snapshot_meta: dict[str, Any] = {
            "client_order_id": broker_order.client_order_id,
            "previous_filled_qty": str(previous_filled_qty),
            "previous_avg_fill_price": (
                str(previous_avg_fill_price) if previous_avg_fill_price is not None else None
            ),
            "broker_update_timestamp": broker_order.updated_at.isoformat(),
        }
        if received_at is not None:
            snapshot_meta["received_at"] = received_at.isoformat()

        fill = Fill(
            fill_id=str(uuid4()),
            broker_order_id=broker_order.broker_order_id,
            intent_id=broker_order.intent_id,
            run_id=broker_order.run_id,
            timestamp=broker_order.updated_at,
            symbol=broker_order.symbol,
            side=broker_order.side,
            quantity=delta_qty,
            price=broker_order.avg_fill_price,
            fees=None,
            liquidity=self._infer_liquidity(broker_order),
            venue="alpaca",
            metadata=snapshot_meta,
        )

        return FillExtractionResult(
            fill=fill,
            new_filled_qty=current_filled_qty,
        )

    def _map_order_status(self, status: str) -> OrderStatus:
        normalized = status.lower()

        if normalized in {"new", "accepted", "pending_new", "accepted_for_bidding"}:
            return OrderStatus.SUBMITTED
        if normalized == "partially_filled":
            return OrderStatus.PARTIALLY_FILLED
        if normalized == "filled":
            return OrderStatus.FILLED
        if normalized in {"canceled", "expired", "done_for_day"}:
            return OrderStatus.CANCELED
        if normalized in {"rejected", "suspended"}:
            return OrderStatus.REJECTED

        raise ValueError(f"Unsupported broker status: {status}")

    @staticmethod
    def _parse_side(value: str) -> Side:
        return Side(value.lower())

    @staticmethod
    def _parse_order_type(value: str) -> OrderType:
        return OrderType(value.lower())

    @staticmethod
    def _parse_time_in_force(value: str) -> TimeInForce:
        return TimeInForce(value.lower())

    @staticmethod
    def _infer_liquidity(broker_order: BrokerOrder) -> LiquiditySide | None:
        return None

    @staticmethod
    def _extract_last_error(payload: dict[str, Any]) -> str | None:
        if payload.get("reject_reason"):
            return str(payload["reject_reason"])
        return None

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if value is None:
            return Decimal("0")
        return Decimal(str(value))

    @staticmethod
    def _to_decimal_or_none(value: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        raise TypeError(f"Unsupported datetime value: {value!r}")
