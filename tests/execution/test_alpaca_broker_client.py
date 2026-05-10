from types import SimpleNamespace

import pytest

from autonomous_trading_platform.execution.clients import alpaca_broker_client
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient


class CapturingHttpClient:
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _settings(**overrides):
    data = {
        "broker_api_key": "paper-key",
        "broker_api_secret": "paper-secret",
        "alpaca_base_url": "https://paper-api.alpaca.markets",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_alpaca_broker_client_uses_selected_broker_credentials(monkeypatch) -> None:
    CapturingHttpClient.calls.clear()
    monkeypatch.setattr(alpaca_broker_client.httpx, "Client", CapturingHttpClient)

    AlpacaBrokerClient(_settings())

    call = CapturingHttpClient.calls[0]
    assert call["base_url"] == "https://paper-api.alpaca.markets"
    assert call["headers"]["APCA-API-KEY-ID"] == "paper-key"
    assert call["headers"]["APCA-API-SECRET-KEY"] == "paper-secret"


def test_alpaca_broker_client_uses_live_base_url_when_selected(monkeypatch) -> None:
    CapturingHttpClient.calls.clear()
    monkeypatch.setattr(alpaca_broker_client.httpx, "Client", CapturingHttpClient)

    AlpacaBrokerClient(
        _settings(
            broker_api_key="live-key",
            broker_api_secret="live-secret",
            alpaca_base_url="https://api.alpaca.markets",
        )
    )

    call = CapturingHttpClient.calls[0]
    assert call["base_url"] == "https://api.alpaca.markets"
    assert call["headers"]["APCA-API-KEY-ID"] == "live-key"
    assert call["headers"]["APCA-API-SECRET-KEY"] == "live-secret"


@pytest.mark.parametrize(
    ("api_key", "api_secret"),
    [
        (None, "secret"),
        ("key", None),
        ("", "secret"),
        ("key", ""),
    ],
)
def test_alpaca_broker_client_rejects_missing_credentials(api_key, api_secret) -> None:
    with pytest.raises(ValueError, match="Missing Alpaca credentials"):
        AlpacaBrokerClient(
            _settings(
                broker_api_key=api_key,
                broker_api_secret=api_secret,
            )
        )


def test_alpaca_broker_client_rejects_invalid_base_url() -> None:
    with pytest.raises(ValueError, match="Invalid Alpaca base URL"):
        AlpacaBrokerClient(_settings(alpaca_base_url="https://example.test"))
