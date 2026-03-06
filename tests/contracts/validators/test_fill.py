from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import LiquiditySide, Side
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.fill import FILL_RULES


def make_fill(**overrides) -> Fill:
    data = {
        "fill_id": "fill_123",
        "broker_order_id": "bo_123",
        "intent_id": uuid4(),
        "run_id": uuid4(),
        "timestamp": datetime.now(UTC),
        "symbol": "AAPL",
        "side": Side.BUY,
        "quantity": Decimal("10"),
        "price": Decimal("150"),
        "fees": Decimal("1.25"),
        "liquidity": LiquiditySide.TAKER,
        "venue": "NASDAQ",
        "metadata": None,
    }

    data.update(overrides)

    return Fill(**data)


def test_valid_fill_passes():
    fill = make_fill()

    result = run_rules(fill, FILL_RULES)

    assert result.ok
    assert result.violations == []


def test_quantity_must_be_positive():
    fill = make_fill(quantity=Decimal("0"))

    result = run_rules(fill, FILL_RULES)

    assert not result.ok
    assert any(v.code == "QUANTITY_POSITIVE" for v in result.violations)


def test_price_must_be_positive():
    fill = make_fill(price=Decimal("0"))

    result = run_rules(fill, FILL_RULES)

    assert not result.ok
    assert any(v.code == "PRICE_POSITIVE" for v in result.violations)


def test_fees_must_be_non_negative_when_present():
    fill = make_fill(fees=Decimal("-1"))

    result = run_rules(fill, FILL_RULES)

    assert not result.ok
    assert any(v.code == "FEES_NON_NEGATIVE_WHEN_PRESENT" for v in result.violations)
