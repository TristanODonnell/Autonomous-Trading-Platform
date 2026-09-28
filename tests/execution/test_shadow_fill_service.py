from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import OrderType, Side, TimeInForce
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    _BarProxy,
    build_platform_execution_service,
)
from autonomous_trading_platform.execution.services.shadow_fill_service import ShadowFillService

_NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)


def _intent(symbol: str, qty: str, side: Side = Side.BUY) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        run_id=uuid4(),
        strategy_id="ondeck",
        timestamp=_NOW,
        bar_timestamp=_NOW,
        symbol=symbol,
        side=side,
        qty=Decimal(qty),
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        client_order_id=f"c-{symbol}-{qty}",
        idempotency_key=f"k-{symbol}-{qty}",
        extended_hours=False,
    )


def _bar(close: str, volume: str) -> _BarProxy:
    price = Decimal(close)
    return _BarProxy(
        open=price, high=price, low=price, close=price, volume=Decimal(volume), timestamp=_NOW
    )


def test_thin_bar_partial_fill_matches_the_broker_execution_model() -> None:
    bar = _bar("100", "1000")  # 5% participation cap -> at most 50 shares
    intent = _intent("AAPL", "80")
    service = ShadowFillService(bar_source=lambda _: bar, timestamp=_NOW)

    result = service.fill([intent], prices={"AAPL": 100.0})

    expected = build_platform_execution_service().fill(
        order_intents=[intent], bars_at_timestamp={"AAPL": bar}
    )
    assert [(f.quantity, f.price) for f in result.fills] == [
        (f.quantity, f.price) for f in expected.fills
    ]
    assert result.fills[0].quantity == Decimal("50")
    assert result.unfilled_qty == {intent.intent_id: Decimal("30")}
    assert result.partial_count == 1
    assert result.basis == {"AAPL": "bar"}


def test_fills_are_deterministic() -> None:
    bar = _bar("100", "1000000")
    intent = _intent("AAPL", "10")

    first = ShadowFillService(bar_source=lambda _: bar, timestamp=_NOW).fill([intent], prices={})
    second = ShadowFillService(bar_source=lambda _: bar, timestamp=_NOW).fill([intent], prices={})

    assert [(f.fill_id, f.quantity, f.price) for f in first.fills] == [
        (f.fill_id, f.quantity, f.price) for f in second.fills
    ]


def test_without_a_bar_fills_at_the_cycle_price_with_slippage() -> None:
    intent = _intent("AAPL", "250")
    service = ShadowFillService(bar_source=lambda _: None, timestamp=_NOW)

    result = service.fill([intent], prices={"AAPL": 100.0})

    assert result.basis == {"AAPL": "price"}
    assert result.fills[0].quantity == Decimal("250")  # no volume, so no participation cap
    assert result.fills[0].price > Decimal("100")  # adverse slippage on a buy
    assert result.unfilled_qty == {}


def test_without_a_bar_or_price_nothing_fills() -> None:
    intent = _intent("AAPL", "10")
    service = ShadowFillService(bar_source=lambda _: None, timestamp=_NOW)

    result = service.fill([intent], prices={})

    assert result.fills == []
    assert result.basis == {"AAPL": "none"}
    assert result.unfilled_qty == {intent.intent_id: Decimal("10")}


def test_uses_the_simulated_brokers_own_bars() -> None:
    bar = _bar("42", "1000000")

    class _Broker:
        def bar_for(self, symbol: str) -> _BarProxy | None:
            return bar if symbol == "AAPL" else None

    service = ShadowFillService.for_broker(_Broker(), session=None, timestamp=_NOW)
    result = service.fill([_intent("AAPL", "5")], prices={"AAPL": 999.0})

    assert result.basis == {"AAPL": "bar"}
    # The bar close plus a sliver of volume-share slippage, not the passed price.
    assert Decimal("42") <= result.fills[0].price < Decimal("42.01")
