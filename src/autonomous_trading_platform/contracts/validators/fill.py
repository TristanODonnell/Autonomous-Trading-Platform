# autonomous_trading_platform/contracts/validators/fill.py

from __future__ import annotations

from autonomous_trading_platform.contracts.trading.fill import Fill

from .core import Rule, is_positive

FILL_RULES: list[Rule[Fill]] = [
    Rule(
        code="QUANTITY_POSITIVE",
        field="quantity",
        check=lambda fill, _ctx: is_positive(fill.quantity),
        message=lambda fill, _ctx: "quantity must be > 0",
    ),
    Rule(
        code="PRICE_POSITIVE",
        field="price",
        check=lambda fill, _ctx: is_positive(fill.price),
        message=lambda fill, _ctx: "price must be > 0",
    ),
    Rule(
        code="FEES_NON_NEGATIVE_WHEN_PRESENT",
        field="fees",
        check=lambda fill, _ctx: fill.fees is None or fill.fees >= 0,
        message=lambda fill, _ctx: f"fees must be >= 0 when present (fees={fill.fees})",
    ),
]
