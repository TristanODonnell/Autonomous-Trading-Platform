# autonomous_trading_platform/contracts/validators/broker_order.py

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder

from .core import Rule, is_non_negative, is_positive

BROKER_ORDER_RULES: list[Rule[BrokerOrder]] = [
    Rule(
        code="ORDER_FILLED_QTY_NON_NEGATIVE",
        field="filled_qty",
        check=lambda order, _ctx: is_non_negative(order.filled_qty),
        message=lambda order, _ctx: "filled_qty must be >= 0",
    ),
    Rule(
        code="ORDER_REQUESTED_QTY_PRESENT_WHEN_EXECUTED",
        field="requested_qty",
        check=lambda order, _ctx: (
            order.status not in {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED}
            or order.requested_qty is not None
        ),
        message=lambda order, _ctx: (
            f"requested_qty must be present when status='{order.status.value}'"
        ),
    ),
    Rule(
        code="ORDER_FILLED_QTY_EQUALS_REQUESTED_QTY_WHEN_FILLED",
        field=None,
        check=lambda order, _ctx: (
            order.status != OrderStatus.FILLED
            or (order.requested_qty is not None and order.filled_qty == order.requested_qty)
        ),
        message=lambda order, _ctx: (
            "when status='filled', filled_qty must equal requested_qty "
            f"(filled_qty={order.filled_qty}, requested_qty={order.requested_qty})"
        ),
    ),
    Rule(
        code="ORDER_FILLED_QTY_POSITIVE_WHEN_FILLED",
        field="filled_qty",
        check=lambda order, _ctx: (
            order.status != OrderStatus.FILLED or is_positive(order.filled_qty)
        ),
        message=lambda order, _ctx: (
            f"when status='filled', filled_qty must be > 0 (filled_qty={order.filled_qty})"
        ),
    ),
    Rule(
        code="ORDER_FILLED_QTY_STRICTLY_BETWEEN_ZERO_AND_REQUESTED_QTY_WHEN_PARTIALLY_FILLED",
        field=None,
        check=lambda order, _ctx: (
            order.status != OrderStatus.PARTIALLY_FILLED
            or (order.requested_qty is not None and 0 < order.filled_qty < order.requested_qty)
        ),
        message=lambda order, _ctx: (
            "when status='partially_filled', filled_qty must satisfy "
            "0 < filled_qty < requested_qty "
            f"(filled_qty={order.filled_qty}, requested_qty={order.requested_qty})"
        ),
    ),
]
