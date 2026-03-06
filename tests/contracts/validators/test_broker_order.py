from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder
from autonomous_trading_platform.contracts.validators.broker_order import BROKER_ORDER_RULES
from autonomous_trading_platform.contracts.validators.core import run_rules


def make_broker_order(**overrides) -> BrokerOrder:
    data = {
        "broker_order_id": "bo_123",
        "client_order_id": "co_123",
        "intent_id": uuid4(),
        "run_id": uuid4(),
        "broker": "alpaca",
        "account_id": "acct_001",
        "symbol": "AAPL",
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
        "extended_hours": False,
        "qty": Decimal("10"),
        "notional": None,
        "limit_price": None,
        "stop_price": None,
        "status": OrderStatus.NEW,
        "submitted_at": None,
        "updated_at": datetime.now(UTC),
        "filled_qty": Decimal("0"),
        "avg_fill_price": None,
        "last_error": None,
        "raw_broker_payload": None,
        "requested_qty": Decimal("10"),
    }

    data.update(overrides)

    return BrokerOrder(**data)


def test_valid_broker_order_passes():
    order = make_broker_order()

    result = run_rules(order, BROKER_ORDER_RULES)

    assert result.ok
    assert result.violations == []


def test_negative_filled_qty_fails():
    order = make_broker_order(filled_qty=Decimal("-1"))

    result = run_rules(order, BROKER_ORDER_RULES)

    assert not result.ok
    assert any(v.code == "ORDER_FILLED_QTY_NON_NEGATIVE" for v in result.violations)


def test_filled_requires_requested_qty():
    order = make_broker_order(status=OrderStatus.FILLED, requested_qty=None)

    result = run_rules(order, BROKER_ORDER_RULES)

    assert not result.ok
    assert any(v.code == "ORDER_REQUESTED_QTY_PRESENT_WHEN_EXECUTED" for v in result.violations)


def test_filled_qty_must_equal_requested_qty():
    order = make_broker_order(
        status=OrderStatus.FILLED, requested_qty=Decimal("10"), filled_qty=Decimal("7")
    )

    result = run_rules(order, BROKER_ORDER_RULES)

    assert not result.ok
    assert any(
        v.code == "ORDER_FILLED_QTY_EQUALS_REQUESTED_QTY_WHEN_FILLED" for v in result.violations
    )


def test_partial_fill_bounds():
    order = make_broker_order(
        status=OrderStatus.PARTIALLY_FILLED, requested_qty=Decimal("10"), filled_qty=Decimal("10")
    )

    result = run_rules(order, BROKER_ORDER_RULES)

    assert not result.ok
    assert any(
        v.code == "ORDER_FILLED_QTY_STRICTLY_BETWEEN_ZERO_AND_REQUESTED_QTY_WHEN_PARTIALLY_FILLED"
        for v in result.violations
    )


def test_valid_filled_order_passes():
    order = make_broker_order(
        status=OrderStatus.FILLED, requested_qty=Decimal("10"), filled_qty=Decimal("10")
    )

    result = run_rules(order, BROKER_ORDER_RULES)

    assert result.ok


def test_valid_partial_fill_passes():
    order = make_broker_order(
        status=OrderStatus.PARTIALLY_FILLED, requested_qty=Decimal("10"), filled_qty=Decimal("5")
    )

    result = run_rules(order, BROKER_ORDER_RULES)

    assert result.ok
