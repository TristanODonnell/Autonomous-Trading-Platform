from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    MarketSession,
    OrderType,
    PriceBasis,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.contracts.runtime.universe_snapshot import (
    UniverseSnapshot,
)
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.ingestion.helpers.bar_identity import build_bar_id


def make_universe_snapshot(
    *,
    universe_id: str = "00000000-0000-0000-0000-000000000301",
    snapshot_date: date = date(2025, 1, 15),
    effective_start: datetime = datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
    effective_end: datetime | None = None,
    symbols: list[str] | None = None,
    criteria: dict[str, Any] | None = None,
    version: str = "test-version",
    source: str = "test",
    built_at: datetime = datetime(2025, 1, 15, 14, 31, tzinfo=UTC),
    notes: str | None = None,
) -> UniverseSnapshot:
    return UniverseSnapshot(
        universe_id=universe_id,
        snapshot_date=snapshot_date,
        effective_start=effective_start,
        effective_end=effective_end,
        symbols=symbols if symbols is not None else ["AAPL", "MSFT"],
        criteria=criteria if criteria is not None else {},
        version=version,
        source=source,
        built_at=built_at,
        notes=notes,
    )


def _resolve_bar_interval_one_min() -> BarInterval:
    for candidate in ("ONE_MIN", "ONE_MINUTE", "MINUTE", "ONE_MIN_BAR"):
        if hasattr(BarInterval, candidate):
            return cast(BarInterval, getattr(BarInterval, candidate))
    raise AssertionError("Could not find a 1-minute BarInterval member.")


def _resolve_bar_interval_five_min() -> BarInterval:
    for candidate in ("FIVE_MIN", "FIVE_MINUTE", "FIVE_MIN_BAR"):
        if hasattr(BarInterval, candidate):
            return cast(BarInterval, getattr(BarInterval, candidate))
    raise AssertionError("Could not find a 5-minute BarInterval member.")


def _interval_duration(interval: BarInterval) -> timedelta:
    interval_name = getattr(interval, "name", str(interval))

    if interval_name in {"ONE_MIN", "ONE_MINUTE", "MINUTE", "ONE_MIN_BAR"}:
        return timedelta(minutes=1)
    if interval_name in {"FIVE_MIN", "FIVE_MINUTE", "FIVE_MIN_BAR"}:
        return timedelta(minutes=5)

    raise AssertionError(f"Unsupported interval for test factory duration mapping: {interval_name}")


def make_market_bar(
    *,
    timestamp: datetime,
    interval: BarInterval,
    symbol: str = "AAPL",
    open_price: str = "100.00",
    high_price: str = "101.00",
    low_price: str = "99.00",
    close_price: str = "100.50",
    volume: int = 100,
    trade_count: int | None = 10,
    vwap: str | None = "100.25",
    price_basis: PriceBasis = PriceBasis.RAW,
    adjustment_factor: str = "1",
    session: MarketSession = MarketSession.REGULAR,
    source: str = "test",
) -> MarketBar:
    return MarketBar(
        bar_id=build_bar_id(
            symbol=symbol,
            timestamp=timestamp,
            interval=interval,
            price_basis=price_basis,
        ),
        timestamp=timestamp,
        end_timestamp=timestamp + _interval_duration(interval),
        interval=interval,
        symbol=symbol,
        open=Decimal(open_price),
        high=Decimal(high_price),
        low=Decimal(low_price),
        close=Decimal(close_price),
        volume=volume,
        vwap=Decimal(vwap) if vwap is not None else None,
        trade_count=trade_count,
        price_basis=price_basis,
        adjustment_factor=Decimal(adjustment_factor),
        source=source,
        ingested_at=datetime.now(UTC),
        quality_flags=[],
        session=session,
    )


def make_minute_bar(
    *,
    timestamp: datetime,
    symbol: str = "AAPL",
    open_price: str = "100.00",
    high_price: str = "101.00",
    low_price: str = "99.00",
    close_price: str = "100.50",
    volume: int = 100,
    trade_count: int | None = 10,
    vwap: str | None = "100.25",
    price_basis: PriceBasis = PriceBasis.RAW,
    adjustment_factor: str = "1",
    session: MarketSession = MarketSession.REGULAR,
    source: str = "test",
) -> MarketBar:
    return make_market_bar(
        timestamp=timestamp,
        interval=_resolve_bar_interval_one_min(),
        symbol=symbol,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        trade_count=trade_count,
        vwap=vwap,
        price_basis=price_basis,
        adjustment_factor=adjustment_factor,
        session=session,
        source=source,
    )


def make_five_minute_bar(
    *,
    timestamp: datetime,
    symbol: str = "AAPL",
    open_price: str = "100.00",
    high_price: str = "101.00",
    low_price: str = "99.00",
    close_price: str = "100.50",
    volume: int = 100,
    trade_count: int | None = 10,
    vwap: str | None = "100.25",
    price_basis: PriceBasis = PriceBasis.RAW,
    adjustment_factor: str = "1",
    session: MarketSession = MarketSession.REGULAR,
    source: str = "test",
) -> MarketBar:
    return make_market_bar(
        timestamp=timestamp,
        interval=_resolve_bar_interval_five_min(),
        symbol=symbol,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        trade_count=trade_count,
        vwap=vwap,
        price_basis=price_basis,
        adjustment_factor=adjustment_factor,
        session=session,
        source=source,
    )


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
