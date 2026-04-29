from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)


@dataclass
class MockIntent:
    intent_id: str
    run_id: str
    symbol: str
    side: str
    qty: Decimal
    order_type: str
    limit_price: Decimal | None = None


@dataclass
class MockBar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass
class MockCosts:
    fill_price: Decimal
    commission: Decimal


class MockCostModel:
    def apply_costs(self, *, side, reference_price, quantity):
        return MockCosts(
            fill_price=Decimal(str(reference_price)),
            commission=Decimal("0"),
        )


def test_limit_buy_fills_when_bar_low_crosses_limit():
    service = SimulatedExecutionService(
        simulation_cost_model_service=MockCostModel(),
    )

    intent = MockIntent(
        intent_id=str(uuid4()),
        run_id=str(uuid4()),
        symbol="AAPL",
        side="buy",
        qty=Decimal("10"),
        order_type="limit",
        limit_price=Decimal("100"),
    )

    bar = MockBar(
        symbol="AAPL",
        timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
        open=Decimal("102"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("102"),
    )

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": bar},
    )

    assert len(fills) == 1
    assert fills[0].symbol == "AAPL"
    assert fills[0].side == "buy"
    assert fills[0].quantity == Decimal("10")
    assert fills[0].price == Decimal("100")


def test_limit_buy_does_not_fill_when_bar_low_does_not_cross_limit():
    service = SimulatedExecutionService(
        simulation_cost_model_service=MockCostModel(),
    )

    intent = MockIntent(
        intent_id=str(uuid4()),
        run_id=str(uuid4()),
        symbol="AAPL",
        side="buy",
        qty=Decimal("10"),
        order_type="limit",
        limit_price=Decimal("100"),
    )

    bar = MockBar(
        symbol="AAPL",
        timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
        open=Decimal("102"),
        high=Decimal("105"),
        low=Decimal("101"),
        close=Decimal("102"),
    )

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": bar},
    )

    assert fills == []


def test_limit_sell_fills_when_bar_high_crosses_limit():
    service = SimulatedExecutionService(
        simulation_cost_model_service=MockCostModel(),
    )

    intent = MockIntent(
        intent_id=str(uuid4()),
        run_id=str(uuid4()),
        symbol="AAPL",
        side="sell",
        qty=Decimal("10"),
        order_type="limit",
        limit_price=Decimal("100"),
    )

    bar = MockBar(
        symbol="AAPL",
        timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
        open=Decimal("98"),
        high=Decimal("101"),
        low=Decimal("95"),
        close=Decimal("98"),
    )

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": bar},
    )

    assert len(fills) == 1
    assert fills[0].symbol == "AAPL"
    assert fills[0].side == "sell"
    assert fills[0].quantity == Decimal("10")
    assert fills[0].price == Decimal("100")


def test_limit_sell_does_not_fill_when_bar_high_does_not_cross_limit():
    service = SimulatedExecutionService(
        simulation_cost_model_service=MockCostModel(),
    )

    intent = MockIntent(
        intent_id=str(uuid4()),
        run_id=str(uuid4()),
        symbol="AAPL",
        side="sell",
        qty=Decimal("10"),
        order_type="limit",
        limit_price=Decimal("100"),
    )

    bar = MockBar(
        symbol="AAPL",
        timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
        open=Decimal("98"),
        high=Decimal("99"),
        low=Decimal("95"),
        close=Decimal("98"),
    )

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": bar},
    )

    assert fills == []
