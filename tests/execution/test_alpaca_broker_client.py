from types import SimpleNamespace

import pytest

from autonomous_trading_platform.execution.clients import alpaca_broker_client
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient


class CapturingHttpClient:
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.calls.append(kwargs)


class FakeResponse:
    status_code = 200
    is_error = False

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"id": "order-1"}


class RequestCapturingHttpClient:
    init_calls: list[dict] = []
    requests: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.init_calls.append(kwargs)

    def request(self, method, path, **kwargs):
        self.requests.append({"method": method, "path": path, **kwargs})
        return FakeResponse()


class CapturingMetric:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict]] = []

    def add(self, value, labels) -> None:
        self.calls.append((value, labels))

    def record(self, value, labels) -> None:
        self.calls.append((value, labels))


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


def test_alpaca_broker_client_adds_request_id_and_endpoint_instrumentation(
    monkeypatch,
) -> None:
    RequestCapturingHttpClient.init_calls.clear()
    RequestCapturingHttpClient.requests.clear()
    monkeypatch.setattr(alpaca_broker_client.httpx, "Client", RequestCapturingHttpClient)

    client = AlpacaBrokerClient(_settings())
    response = client.submit_order({"symbol": "AAPL"})

    request = RequestCapturingHttpClient.requests[0]
    assert response == {"id": "order-1"}
    assert request["method"] == "POST"
    assert request["path"] == "/v2/orders"
    assert request["headers"]["X-RATP-Request-ID"].startswith("ratp-")


def test_alpaca_broker_client_broker_metrics_use_dashboard_safe_labels(monkeypatch) -> None:
    RequestCapturingHttpClient.init_calls.clear()
    RequestCapturingHttpClient.requests.clear()
    requests_metric = CapturingMetric()
    latency_metric = CapturingMetric()
    monkeypatch.setattr(alpaca_broker_client.httpx, "Client", RequestCapturingHttpClient)
    monkeypatch.setattr(alpaca_broker_client, "ratp_broker_api_requests_total", requests_metric)
    monkeypatch.setattr(alpaca_broker_client, "ratp_broker_api_latency_seconds", latency_metric)

    client = AlpacaBrokerClient(_settings())
    client.submit_order({"symbol": "AAPL"})

    request_labels = requests_metric.calls[0][1]
    latency_labels = latency_metric.calls[0][1]
    assert request_labels == {
        "environment": "unknown",
        "component": "execution.broker_client",
        "broker": "alpaca",
        "endpoint": "orders.submit",
        "status": "started",
    }
    assert latency_labels["status"] == "200"
    assert "correlation_id" not in latency_labels
    assert "job_run_id" not in latency_labels
    assert "run_id" not in latency_labels
