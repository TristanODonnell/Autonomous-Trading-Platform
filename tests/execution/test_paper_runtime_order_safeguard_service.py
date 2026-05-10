from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from autonomous_trading_platform.contracts.common.enums import OrderType, Side, TimeInForce
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.errors import OrderNotAllowedForSubmissionError
from autonomous_trading_platform.execution.services.broker_adaptor import AlpacaBrokerAdapter
from autonomous_trading_platform.execution.services.order_execution_service import (
    OrderExecutionService,
)
from autonomous_trading_platform.execution.services.paper_runtime_order_safeguard_service import (
    PaperRuntimeOrderSafeguardConfig,
    PaperRuntimeOrderSafeguardService,
)
from tests.utilities.paper_trading_cycle_fixture import PAPER_CYCLE_NOW_UTC


def _intent(
    *,
    symbol: str = "AAPL",
    qty: Decimal = Decimal("1"),
    order_type: OrderType = OrderType.LIMIT,
    limit_price: Decimal | None = Decimal("0.01"),
) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        idempotency_key="paper-runtime-guard-test",
        run_id=uuid4(),
        strategy_id="baseline_strategy",
        timestamp=PAPER_CYCLE_NOW_UTC,
        bar_timestamp=PAPER_CYCLE_NOW_UTC,
        symbol=symbol,
        side=Side.BUY,
        qty=qty,
        notional=None,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        extended_hours=False,
        client_order_id=f"paper-runtime-guard-{uuid4()}",
        metadata=None,
    )


def _guard() -> PaperRuntimeOrderSafeguardService:
    return PaperRuntimeOrderSafeguardService(
        PaperRuntimeOrderSafeguardConfig(
            enabled=True,
            max_order_qty=Decimal("1"),
            max_order_notional=Decimal("1.00"),
            max_limit_price=Decimal("1.00"),
            allowed_symbols=frozenset({"AAPL"}),
            require_limit_orders=True,
        )
    )


class _RecordingClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def submit_order(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"id": "paper-runtime-guard-order", **payload}


def test_tiny_paper_runtime_guard_allows_safe_limit_order() -> None:
    client = _RecordingClient()
    service = OrderExecutionService(
        broker_client=client,
        adapter=AlpacaBrokerAdapter(),
        paper_runtime_order_guard=_guard(),
    )

    response = service.submit(_intent())

    assert response["id"] == "paper-runtime-guard-order"
    assert client.payloads[0]["qty"] == "1"


def test_tiny_paper_runtime_guard_blocks_oversized_qty_before_submission() -> None:
    client = _RecordingClient()
    service = OrderExecutionService(
        broker_client=client,
        adapter=AlpacaBrokerAdapter(),
        paper_runtime_order_guard=_guard(),
    )

    with pytest.raises(OrderNotAllowedForSubmissionError, match="qty 2 exceeds max 1"):
        service.submit(_intent(qty=Decimal("2")))

    assert client.payloads == []


def test_tiny_paper_runtime_guard_blocks_market_order_before_submission() -> None:
    client = _RecordingClient()
    service = OrderExecutionService(
        broker_client=client,
        adapter=AlpacaBrokerAdapter(),
        paper_runtime_order_guard=_guard(),
    )

    with pytest.raises(OrderNotAllowedForSubmissionError, match="requires limit orders"):
        service.submit(
            _intent(
                order_type=OrderType.MARKET,
                limit_price=None,
            )
        )

    assert client.payloads == []


def test_tiny_paper_runtime_guard_blocks_disallowed_symbol_before_submission() -> None:
    client = _RecordingClient()
    service = OrderExecutionService(
        broker_client=client,
        adapter=AlpacaBrokerAdapter(),
        paper_runtime_order_guard=_guard(),
    )

    with pytest.raises(OrderNotAllowedForSubmissionError, match="not in the allowed"):
        service.submit(_intent(symbol="MSFT"))

    assert client.payloads == []
