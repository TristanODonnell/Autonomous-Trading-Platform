from __future__ import annotations

from typing import Any

import pytest

from autonomous_trading_platform.ingestion.corporate_actions.clients import (
    alpaca_corporate_action_client as client,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _install_fake_http(monkeypatch: pytest.MonkeyPatch, pages: list[dict[str, Any]]) -> list[dict]:
    calls: list[dict] = []
    remaining = list(pages)

    def fake_get(url: str, *, headers: dict, params: dict, timeout: int) -> _FakeResponse:
        calls.append({"url": url, "params": dict(params)})
        return _FakeResponse(remaining.pop(0))

    monkeypatch.setattr(client.httpx, "get", fake_get)

    class _Settings:
        broker_api_key = "key"
        broker_api_secret = "secret"

    monkeypatch.setattr(client, "Settings", lambda: _Settings())
    return calls


def test_fetch_keeps_every_list_and_merges_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: list[dict[str, Any]] = [
        {
            "corporate_actions": {
                "cash_dividends": [{"id": "d1", "symbol": "AAPL"}],
                "forward_splits": [{"id": "s1", "symbol": "NVDA"}],
                "stock_mergers": [{"id": "m1", "acquiree_symbol": "PXD"}],
            },
            "next_page_token": "page-2",
        },
        {
            "corporate_actions": {
                "cash_dividends": [{"id": "d2", "symbol": "MSFT"}],
                "unit_splits": [{"id": "u1", "old_symbol": "DHACU"}],
            },
            "next_page_token": None,
        },
    ]
    calls = _install_fake_http(monkeypatch, pages)

    result = client.fetch_corporate_actions(
        start="2024-01-01",
        end="2024-12-31",
        symbols=["nvda", "AAPL"],
        types=["forward_split", "cash_dividend"],
    )

    assert result == {
        "corporate_actions": {
            "cash_dividends": [{"id": "d1", "symbol": "AAPL"}, {"id": "d2", "symbol": "MSFT"}],
            "forward_splits": [{"id": "s1", "symbol": "NVDA"}],
            "stock_mergers": [{"id": "m1", "acquiree_symbol": "PXD"}],
            "unit_splits": [{"id": "u1", "old_symbol": "DHACU"}],
        },
        "next_page_token": None,
    }
    assert len(calls) == 2
    assert calls[0]["url"] == client.CORPORATE_ACTIONS_URL
    assert calls[0]["params"] == {
        "limit": 1000,
        "sort": "asc",
        "start": "2024-01-01",
        "end": "2024-12-31",
        "symbols": "NVDA,AAPL",
        "types": "forward_split,cash_dividend",
    }
    assert calls[1]["params"]["page_token"] == "page-2"


def test_fetch_without_filters_sends_only_limit_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_http(monkeypatch, [{"corporate_actions": {}, "next_page_token": None}])

    result = client.fetch_corporate_actions(limit=5000)

    assert result == {"corporate_actions": {}, "next_page_token": None}
    assert calls[0]["params"] == {"limit": 1000, "sort": "asc"}
