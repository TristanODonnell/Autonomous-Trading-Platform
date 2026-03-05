# autonomous_trading_platform/contracts/validators/broker_order.py

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder

from .core import Rule, is_non_negative, is_positive

BROKER_ORDER_RULES: list[Rule[BrokerOrder]] = [
    # filled_qty >= 0
    Rule(
        code="ORDER_FILLED_QTY_NONNEG",
        field="filled_qty",
        check=lambda order, _ctx: is_non_negative(order.filled_qty),
        message=lambda order, _ctx: "filled_qty must be >= 0",
    ),
    # If status == filled then filled_qty == requested_qty (or broker final)
    Rule(
        code="ORDER_FILLED_QTY_EQUALS_REQUESTED_WHEN_FILLED",
        field="filled_qty",
        check=lambda order, _ctx: (
            (order.status != OrderStatus.FILLED) or (order.filled_qty == order.requested_qty)
        ),
        message=lambda order, _ctx: (
            f"status='filled' requires filled_qty == requested_qty "
            f"(filled_qty={order.filled_qty}, requested_qty={order.requested_qty})"
        ),
    ),
    # If status == filled then filled_qty > 0
    Rule(
        code="ORDER_FILLED_QTY_POSITIVE_WHEN_FILLED",
        field="filled_qty",
        check=lambda order, _ctx: (
            (order.status != OrderStatus.FILLED) or is_positive(order.filled_qty)
        ),
        message=lambda order, _ctx: (
            f"status='filled' requires filled_qty > 0 (got {order.filled_qty})"
        ),
    ),
    # If status == partially_filled then 0 < filled_qty < requested_qty
    Rule(
        code="ORDER_FILLED_QTY_BOUNDS_WHEN_PARTIALLY_FILLED",
        field="filled_qty",
        check=lambda order, _ctx: (
            (order.status != OrderStatus.PARTIALLY_FILLED)
            or (order.requested_qty is not None and 0 < order.filled_qty < order.requested_qty)
        ),
        message=lambda order, _ctx: (
            "status='partially_filled' requires 0 < filled_qty < requested_qty "
            f"(filled_qty={order.filled_qty}, requested_qty={order.requested_qty})"
        ),
    ),
]
