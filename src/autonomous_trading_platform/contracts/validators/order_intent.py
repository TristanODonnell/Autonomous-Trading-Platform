# autonomous_trading_platform/contracts/validators/order_intent.py

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import OrderType
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent

from .core import Rule, is_positive

ORDER_INTENT_RULES: list[Rule[OrderIntent]] = [
    # If order_type="limit",  limit_price is required
    Rule(
        code="ORDER_TYPE_LIMIT_LIMIT_PRICE_THEN_REQUIRED ",
        field="limit_price",
        check=lambda oi, _ctx: (
            (oi.order_type != OrderType.LIMIT) or (isinstance(oi.limit_price, float))
        ),
        message=lambda oi, _ctx: "When order_type is limit, limit price is required",
    ),
    # If order_type="stop" , stop_price is required.
    Rule(
        code="ORDER_TYPE_STOP_STOP_PRICE_THEN_REQUIRED ",
        field="stop_price",
        check=lambda oi, _ctx: (
            (oi.order_type != OrderType.STOP) or (isinstance(oi.stop_price, float))
        ),
        message=lambda oi, _ctx: "When order_type is limit, limit price is required",
    ),
    # If order_type="stop_limit" , both stop_price and limit_price required.
    Rule(
        code="ORDER_TYPE_STOP_LIMIT_STOP_AND_LIMIT_PRICE_THEN_REQUIRED ",
        field="stop_price, limit_price",
        check=lambda oi, _ctx: (
            (oi.order_type != OrderType.STOP_LIMIT)
            or (isinstance(oi.stop_price, float) and isinstance(oi.limit_price, float))
        ),
        message=lambda oi, _ctx: "When order_type is limit, limit price is required",
    ),
    # qty > 0 if set;
    Rule(
        code="QUANTITY_POSITIVE_IF_SET ",
        field="qty",
        check=lambda oi, _ctx: (oi.qty is None) or (is_positive(oi.qty)),
        message=lambda oi, _ctx: "When quantity is set, must be positive",
    ),
    # notional > 0 if set.
    Rule(
        code="NOTIONAL_POSITIVE_IF_SET ",
        field="notional",
        check=lambda oi, _ctx: (oi.notional is None) or (is_positive(oi.notional)),
        message=lambda oi, _ctx: "When notional is set, must be positive",
    ),
]
