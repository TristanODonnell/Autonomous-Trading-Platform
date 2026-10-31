"""AlpacaScreenerProvider: pool refresh survives exchange codes the SDK enum lacks."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from autonomous_trading_platform.universe.providers.alpaca_screener_provider import (
    AlpacaScreenerProvider,
)

_AS_OF = date(2026, 10, 30)


def _asset(symbol: str, exchange: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"id-{symbol}",
        "symbol": symbol,
        "exchange": exchange,
        "tradable": True,
        "name": f"{symbol} Inc",
        "marginable": True,
        "shortable": True,
        "fractionable": False,
        "easy_to_borrow": True,
    }
    base.update(extra)
    return base


class _FakeTradingClient:
    """What ``TradingClient(raw_data=True)`` returns: plain dicts, any exchange string."""

    def __init__(self, assets: list[dict[str, Any]]) -> None:
        self._assets = assets
        self.calls = 0

    def get_all_assets(self, filter: Any = None) -> list[dict[str, Any]]:
        self.calls += 1
        return self._assets


class _FakeDataClient:
    def __init__(self, dollar_volume: dict[str, float]) -> None:
        self._dv = dollar_volume
        self.requests: list[Any] = []

    def get_stock_bars(self, request: Any) -> Any:
        self.requests.append(request)
        data: dict[str, list[Any]] = {}
        for symbol in request.symbol_or_symbols:
            if symbol not in self._dv:
                continue
            close = 100.0
            volume = self._dv[symbol] / close
            data[symbol] = [
                SimpleNamespace(
                    timestamp=datetime(2026, 10, 1) + timedelta(days=i), close=close, volume=volume
                )
                for i in range(20)
            ]
        return SimpleNamespace(data=data)


def test_unknown_exchange_codes_are_skipped_not_fatal() -> None:
    assets = [
        _asset("AAPL", "NASDAQ"),
        _asset("SPY", "ARCA"),
        _asset("TXCO", "TXSE"),  # exchange unknown to the installed SDK enum
        _asset("PINK", "OTC"),
        _asset("NOTRADE", "NYSE", tradable=False),
        _asset("BRK.B", "NYSE"),  # symbol pattern excludes dotted classes
        _asset("LOWDV", "NYSE"),
    ]
    trading = _FakeTradingClient(assets)
    data = _FakeDataClient({"AAPL": 5e9, "SPY": 2e10, "TXCO": 1e9, "LOWDV": 1e6, "NOTRADE": 1e9})
    provider = AlpacaScreenerProvider(
        as_of=_AS_OF, top_n=500, trading_client=trading, data_client=data
    )

    records = provider.fetch_symbols()

    assert [r.symbol for r in records] == ["SPY", "AAPL"]  # ranked by dollar volume
    assert trading.calls == 1
    # Only eligible symbols are scored: no TXSE / OTC / untradable / dotted names.
    scored = sorted(s for req in data.requests for s in req.symbol_or_symbols)
    assert scored == ["AAPL", "LOWDV", "SPY"]
    spy = records[0]
    assert spy.exchange == "ARCA" and spy.provider_asset_id == "id-SPY"
    assert spy.is_tradable and spy.marginable is True and spy.fractionable is False


def test_sdk_model_assets_still_work() -> None:
    """Older call sites may still hand over Asset models; both shapes are read."""
    model = SimpleNamespace(**_asset("MSFT", "NASDAQ"))
    provider = AlpacaScreenerProvider(
        as_of=_AS_OF,
        trading_client=_FakeTradingClient([model]),  # type: ignore[list-item]
        data_client=_FakeDataClient({"MSFT": 4e9}),
    )
    assert [r.symbol for r in provider.fetch_symbols()] == ["MSFT"]
