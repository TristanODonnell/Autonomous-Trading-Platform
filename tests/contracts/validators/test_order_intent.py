from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import OrderType, Side, TimeInForce
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.order_intent import ORDER_INTENT_RULES


def make_order_intent(**overrides) -> OrderIntent:
    now = datetime.now(UTC)

    data = {
        "intent_id": uuid4(),
        "idempotency_key": "idem_123",
        "run_id": uuid4(),
        "strategy_id": "strategy_001",
        "timestamp": now,
        "bar_timestamp": now,
        "symbol": "AAPL",
        "side": Side.BUY,
        "qty": Decimal("10"),
        "notional": None,
        "order_type": OrderType.MARKET,
        "limit_price": None,
        "stop_price": None,
        "time_in_force": TimeInForce.DAY,
        "extended_hours": False,
        "client_order_id": "client_123",
        "metadata": None,
    }

    data.update(overrides)
    return OrderIntent(**data)


def test_valid_order_intent_passes():
    order_intent = make_order_intent()

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert result.ok
    assert result.violations == []


def test_limit_order_requires_limit_price():
    order_intent = make_order_intent(
        order_type=OrderType.LIMIT,
        limit_price=None,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "LIMIT_ORDER_REQUIRES_LIMIT_PRICE" for v in result.violations)


def test_stop_order_requires_stop_price():
    order_intent = make_order_intent(
        order_type=OrderType.STOP,
        stop_price=None,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "STOP_ORDER_REQUIRES_STOP_PRICE" for v in result.violations)


def test_stop_limit_order_requires_stop_and_limit_price():
    order_intent = make_order_intent(
        order_type=OrderType.STOP_LIMIT,
        stop_price=None,
        limit_price=None,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(
        v.code == "STOP_LIMIT_ORDER_REQUIRES_STOP_AND_LIMIT_PRICE" for v in result.violations
    )


def test_exactly_one_of_qty_or_notional_must_be_set_when_both_present():
    order_intent = make_order_intent(
        qty=Decimal("10"),
        notional=Decimal("1000"),
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "SIZING_MODE_XOR_QTY_OR_NOTIONAL" for v in result.violations)


def test_exactly_one_of_qty_or_notional_must_be_set_when_neither_present():
    order_intent = make_order_intent(
        qty=None,
        notional=None,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "SIZING_MODE_XOR_QTY_OR_NOTIONAL" for v in result.violations)


def test_qty_must_be_positive_when_set():
    order_intent = make_order_intent(
        qty=Decimal("0"),
        notional=None,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "QTY_POSITIVE_IF_SET" for v in result.violations)


def test_notional_must_be_positive_when_set():
    order_intent = make_order_intent(
        qty=None,
        notional=Decimal("0"),
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "NOTIONAL_POSITIVE_IF_SET" for v in result.violations)


def test_extended_hours_requires_limit_order():
    order_intent = make_order_intent(
        order_type=OrderType.MARKET,
        extended_hours=True,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert not result.ok
    assert any(v.code == "EXTENDED_HOURS_ONLY_LIMIT_ORDER" for v in result.violations)


def test_valid_limit_order_with_limit_price_passes():
    order_intent = make_order_intent(
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert result.ok


def test_valid_stop_order_with_stop_price_passes():
    order_intent = make_order_intent(
        order_type=OrderType.STOP,
        stop_price=Decimal("145"),
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert result.ok


def test_valid_stop_limit_order_passes():
    order_intent = make_order_intent(
        order_type=OrderType.STOP_LIMIT,
        stop_price=Decimal("145"),
        limit_price=Decimal("144"),
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert result.ok


def test_valid_notional_sized_order_passes():
    order_intent = make_order_intent(
        qty=None,
        notional=Decimal("1000"),
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert result.ok


def test_valid_extended_hours_limit_order_passes():
    order_intent = make_order_intent(
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        extended_hours=True,
    )

    result = run_rules(order_intent, ORDER_INTENT_RULES)

    assert result.ok
