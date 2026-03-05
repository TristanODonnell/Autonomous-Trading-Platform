# autonomous_trading_platform/contracts/validators/fill.py

from __future__ import annotations

from autonomous_trading_platform.contracts.trading.fill import Fill

from .core import Rule, is_positive

FILL_RULES: list[Rule[Fill]] = [
    # quantity > 0
    Rule(
        code="QUANTITY_POSITIVE",
        field="quantity",
        check=lambda fill, _ctx: is_positive(fill.quantity),
        message=lambda fill, _ctx: "fill quantity must be positive",
    ),
    # price > 0
    Rule(
        code="PRICE_POSITIVE",
        field="price",
        check=lambda fill, _ctx: is_positive(fill.price),
        message=lambda fill, _ctx: "fill price must be positive",
    ),
]
