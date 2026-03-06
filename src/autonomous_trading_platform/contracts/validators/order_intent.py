# autonomous_trading_platform/contracts/validators/order_intent.py

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import OrderType
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent

from .core import Rule, is_positive

ORDER_INTENT_RULES: list[Rule[OrderIntent]] = [
    Rule(
        code="LIMIT_ORDER_REQUIRES_LIMIT_PRICE",
        field="limit_price",
        check=lambda oi, _ctx: oi.order_type != OrderType.LIMIT or oi.limit_price is not None,
        message=lambda oi, _ctx: "when order_type='limit', limit_price must be set",
    ),
    Rule(
        code="STOP_ORDER_REQUIRES_STOP_PRICE",
        field="stop_price",
        check=lambda oi, _ctx: oi.order_type != OrderType.STOP or oi.stop_price is not None,
        message=lambda oi, _ctx: "when order_type='stop', stop_price must be set",
    ),
    Rule(
        code="STOP_LIMIT_ORDER_REQUIRES_STOP_AND_LIMIT_PRICE",
        field=None,
        check=lambda oi, _ctx: (
            oi.order_type != OrderType.STOP_LIMIT
            or (oi.stop_price is not None and oi.limit_price is not None)
        ),
        message=lambda oi, _ctx: (
            "when order_type='stop_limit', stop_price and limit_price must be set"
        ),
    ),
    Rule(
        code="SIZING_MODE_XOR_QTY_OR_NOTIONAL",
        field=None,
        check=lambda oi, _ctx: (oi.qty is None) != (oi.notional is None),
        message=lambda oi, _ctx: "exactly one of qty or notional must be set",
    ),
    Rule(
        code="QTY_POSITIVE_IF_SET",
        field="qty",
        check=lambda oi, _ctx: oi.qty is None or is_positive(oi.qty),
        message=lambda oi, _ctx: "qty must be > 0 when set",
    ),
    Rule(
        code="NOTIONAL_POSITIVE_IF_SET",
        field="notional",
        check=lambda oi, _ctx: oi.notional is None or is_positive(oi.notional),
        message=lambda oi, _ctx: "notional must be > 0 when set",
    ),
    Rule(
        code="EXTENDED_HOURS_ONLY_LIMIT_ORDER",
        field="extended_hours",
        check=lambda oi, _ctx: not oi.extended_hours or oi.order_type == OrderType.LIMIT,
        message=lambda oi, _ctx: "extended_hours orders must use order_type='limit'",
    ),
]
