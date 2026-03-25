from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


def make_fill(
    *,
    side: Side,
    quantity: str = "10",
    price: str = "100",
    symbol: str = "AAPL",
) -> Fill:
    return Fill(
        fill_id="fill-000000000101",
        broker_order_id="broker-order-000000000102",
        intent_id=UUID("00000000-0000-0000-0000-000000000103"),
        run_id=UUID("00000000-0000-0000-0000-000000000104"),
        timestamp=datetime(2025, 1, 1, 15, 35, tzinfo=UTC),
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


def make_order_intent(
    *,
    intent_id: UUID | None = None,
    idempotency_key: str = "test-idempotency-key",
    run_id: UUID | None = None,
    strategy_id: str = "strategy-alpha",
    timestamp: datetime = datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
    bar_timestamp: datetime = datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    qty: Decimal | None = Decimal("10"),
    notional: Decimal | None = None,
    order_type: OrderType = OrderType.LIMIT,
    limit_price: Decimal | None = Decimal("100"),
    stop_price: Decimal | None = None,
    time_in_force: TimeInForce = TimeInForce.DAY,
    extended_hours: bool = False,
    client_order_id: str = "client-order-000000000203",
    metadata: dict[str, object] | None = None,
) -> OrderIntent:

    if intent_id is None:
        intent_id = UUID("00000000-0000-0000-0000-000000000201")

    if run_id is None:
        run_id = UUID("00000000-0000-0000-0000-000000000202")

    return OrderIntent(
        intent_id=intent_id,
        idempotency_key=idempotency_key,
        run_id=run_id,
        strategy_id=strategy_id,
        timestamp=timestamp,
        bar_timestamp=bar_timestamp,
        symbol=symbol,
        side=side,
        qty=qty,
        notional=notional,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        time_in_force=time_in_force,
        extended_hours=extended_hours,
        client_order_id=client_order_id,
        metadata=metadata,
    )
