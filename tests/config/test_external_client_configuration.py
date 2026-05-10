from types import SimpleNamespace

from autonomous_trading_platform.ingestion.market_data.clients import (
    alpaca_market_data_client,
)


class CapturingHistoricalClient:
    calls: list[tuple[str, str]] = []

    def __init__(self, api_key: str, secret_key: str) -> None:
        self.calls.append((api_key, secret_key))


class CapturingStreamClient:
    calls: list[tuple[str, str]] = []

    def __init__(self, api_key: str, secret_key: str) -> None:
        self.calls.append((api_key, secret_key))


def test_market_data_historical_client_uses_central_broker_credentials(monkeypatch) -> None:
    CapturingHistoricalClient.calls.clear()
    monkeypatch.setattr(
        alpaca_market_data_client,
        "Settings",
        lambda: SimpleNamespace(broker_api_key="paper-key", broker_api_secret="paper-secret"),
    )
    monkeypatch.setattr(
        alpaca_market_data_client,
        "StockHistoricalDataClient",
        CapturingHistoricalClient,
    )

    alpaca_market_data_client.get_stock_historical_client()

    assert CapturingHistoricalClient.calls == [("paper-key", "paper-secret")]


def test_market_data_stream_client_uses_central_broker_credentials(monkeypatch) -> None:
    CapturingStreamClient.calls.clear()
    monkeypatch.setattr(
        alpaca_market_data_client,
        "Settings",
        lambda: SimpleNamespace(broker_api_key="live-key", broker_api_secret="live-secret"),
    )
    monkeypatch.setattr(alpaca_market_data_client, "StockDataStream", CapturingStreamClient)

    alpaca_market_data_client.get_stock_data_stream()

    assert CapturingStreamClient.calls == [("live-key", "live-secret")]
