from __future__ import annotations

from collections.abc import Generator
from contextlib import suppress
from typing import Any

import pytest

from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient

pytestmark = [
    pytest.mark.external,
    pytest.mark.alpaca,
    pytest.mark.smoke,
]
"""
Test Commands:

$env:APP_ENV="test"
$env:DATABASE_URL="postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp"
$env:TRADING_ENVIRONMENT="paper"
$env:VALIDATE_BROKER_CONFIG="true"
$env:ALPACA_EXTERNAL_SMOKE_ENABLED="true"
$env:PAPER_BROKER_API_KEY="your_real_paper_key"
$env:PAPER_BROKER_API_SECRET="your_real_paper_secret"

python -m pytest tests/external/alpaca -m external -v

"""


def _safe_limit_order_payload(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "qty": "1",
        "side": "buy",
        "type": "limit",
        "limit_price": "0.01",
        "time_in_force": "day",
    }


@pytest.fixture()
def submitted_paper_order(
    alpaca_paper_client: AlpacaBrokerClient,
    alpaca_smoke_symbol: str,
) -> Generator[dict[str, Any], None, None]:
    order = alpaca_paper_client.submit_order(_safe_limit_order_payload(alpaca_smoke_symbol))
    try:
        yield order
    finally:
        order_id = order.get("id")
        if isinstance(order_id, str) and order.get("status") not in {
            "canceled",
            "expired",
            "filled",
            "rejected",
        }:
            with suppress(Exception):
                alpaca_paper_client.cancel_order(order_id)


def test_real_alpaca_authentication_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
) -> None:
    account = alpaca_paper_client.get_account()

    assert isinstance(account.get("id"), str)
    assert account["id"]


def test_real_alpaca_account_fetch_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
) -> None:
    account = alpaca_paper_client.get_account()

    for field in ("id", "status", "equity", "buying_power"):
        assert field in account
        assert account[field] is not None


def test_real_alpaca_positions_fetch_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
) -> None:
    positions = alpaca_paper_client.get_positions()

    assert isinstance(positions, list)


def test_real_alpaca_open_orders_fetch_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
) -> None:
    open_orders = alpaca_paper_client.list_open_orders()

    assert isinstance(open_orders, list)


def test_real_alpaca_paper_order_submission_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
    alpaca_smoke_symbol: str,
) -> None:
    order = alpaca_paper_client.submit_order(_safe_limit_order_payload(alpaca_smoke_symbol))
    try:
        assert isinstance(order.get("id"), str)
        assert order["id"]
        assert order.get("symbol") == alpaca_smoke_symbol
        assert order.get("status") in {
            "accepted",
            "new",
            "pending_new",
            "partially_filled",
            "filled",
        }
    finally:
        order_id = order.get("id")
        if isinstance(order_id, str) and order.get("status") not in {
            "canceled",
            "expired",
            "filled",
            "rejected",
        }:
            alpaca_paper_client.cancel_order(order_id)


def test_real_alpaca_order_cancel_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
    submitted_paper_order: dict[str, Any],
) -> None:
    order_id = submitted_paper_order["id"]

    alpaca_paper_client.cancel_order(order_id)
    fetched_order = alpaca_paper_client.get_order_by_id(order_id)

    assert fetched_order["id"] == order_id
    assert fetched_order["status"] in {"canceled", "pending_cancel", "filled", "expired"}


def test_real_alpaca_order_status_fetch_smoke(
    alpaca_paper_client: AlpacaBrokerClient,
    submitted_paper_order: dict[str, Any],
    alpaca_smoke_symbol: str,
) -> None:
    order_id = submitted_paper_order["id"]
    fetched_order = alpaca_paper_client.get_order_by_id(order_id)

    assert fetched_order["id"] == order_id
    assert fetched_order["symbol"] == alpaca_smoke_symbol
    assert isinstance(fetched_order.get("status"), str)
    assert fetched_order["status"]
