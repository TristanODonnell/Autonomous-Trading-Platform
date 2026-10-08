"""The free Alpaca plan cannot query recent SIP data, so bar requests must name a feed."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.data.enums import DataFeed

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.ingestion.market_data.clients import (
    alpaca_market_data_client as client_module,
)
from autonomous_trading_platform.ingestion.market_data.clients.alpaca_historical_bars_client import (
    AlpacaHistoricalBarsClient,
)


class _CapturingClient:
    def __init__(self) -> None:
        self.request: Any = None

    def get_stock_bars(self, request):
        self.request = request
        return SimpleNamespace(data={})


def test_settings_default_feed_is_iex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    assert Settings().alpaca_data_feed == "iex"


def test_settings_feed_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_DATA_FEED", " SIP ")
    assert Settings().alpaca_data_feed == "sip"
    assert client_module.get_data_feed() is DataFeed.SIP


def test_fetch_minute_bars_defaults_to_iex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    capturing = _CapturingClient()
    monkeypatch.setattr(client_module, "get_stock_historical_client", lambda: capturing)

    start = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)
    client_module.fetch_minute_bars(symbols=["AAPL"], start=start, end=start)

    assert capturing.request is not None
    assert capturing.request.feed is DataFeed.IEX


def test_historical_bars_client_passes_feed() -> None:
    capturing = _CapturingClient()
    start = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)

    list(AlpacaHistoricalBarsClient(capturing).fetch_bars(["AAPL"], start, start))
    assert capturing.request.feed is DataFeed.IEX

    list(
        AlpacaHistoricalBarsClient(capturing, feed=DataFeed.SIP).fetch_bars(["AAPL"], start, start)
    )
    assert capturing.request.feed is DataFeed.SIP
