from types import SimpleNamespace

import httpx
import pytest

from autonomous_trading_platform.execution.clients import alpaca_broker_client
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.errors import BrokerOrderRejectedError


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
        "environment": "test",
        "component": "execution.broker_client",
        "broker": "alpaca",
        "endpoint": "orders.submit",
        "status": "started",
    }
    assert latency_labels["status"] == "200"
    assert "correlation_id" not in latency_labels
    assert "job_run_id" not in latency_labels
    assert "run_id" not in latency_labels


def _market_data_client(monkeypatch, **overrides) -> AlpacaBrokerClient:
    RequestCapturingHttpClient.init_calls.clear()
    RequestCapturingHttpClient.requests.clear()
    monkeypatch.setattr(alpaca_broker_client.httpx, "Client", RequestCapturingHttpClient)
    return AlpacaBrokerClient(_settings(**overrides))


def test_latest_trades_go_to_the_market_data_host(monkeypatch) -> None:
    """Regression: trades/latest was sent to paper-api.alpaca.markets, which 404s."""
    client = _market_data_client(monkeypatch)

    client.get_latest_trades(["GOOGL", "META"])

    request = RequestCapturingHttpClient.requests[0]
    assert request["method"] == "GET"
    assert request["path"] == "https://data.alpaca.markets/v2/stocks/trades/latest"
    assert request["params"] == {"symbols": "GOOGL,META", "feed": "iex"}


def test_latest_quotes_go_to_the_market_data_host(monkeypatch) -> None:
    client = _market_data_client(monkeypatch)

    client.get_latest_quotes(["SPY"])

    request = RequestCapturingHttpClient.requests[0]
    assert request["path"] == "https://data.alpaca.markets/v2/stocks/quotes/latest"


def test_market_data_host_and_feed_follow_settings(monkeypatch) -> None:
    client = _market_data_client(
        monkeypatch,
        alpaca_data_base_url="http://mock-data:9000/",
        alpaca_data_feed="sip",
    )

    client.get_latest_trades(["SPY"])

    request = RequestCapturingHttpClient.requests[0]
    assert request["path"] == "http://mock-data:9000/v2/stocks/trades/latest"
    assert request["params"]["feed"] == "sip"


def test_trading_endpoints_still_use_the_trading_host(monkeypatch) -> None:
    client = _market_data_client(monkeypatch)

    client.submit_order({"symbol": "SPY"})

    request = RequestCapturingHttpClient.requests[0]
    assert request["path"] == "/v2/orders"
    assert (
        RequestCapturingHttpClient.init_calls[0]["base_url"] == "https://paper-api.alpaca.markets"
    )


# --- Submit refusals (soak finding 11) ----------------------------------------------


class _StatusResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.is_error = status_code >= 400

    def raise_for_status(self) -> None:
        if self.is_error:
            request = httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )

    def json(self):
        return {"id": "order-1"}


def _client_answering(monkeypatch, status_code: int, text: str = "") -> AlpacaBrokerClient:
    class _HttpClient:
        def __init__(self, **kwargs) -> None:
            pass

        def request(self, method, path, **kwargs):
            return _StatusResponse(status_code, text)

    monkeypatch.setattr(alpaca_broker_client.httpx, "Client", _HttpClient)
    return AlpacaBrokerClient(_settings())


def test_submit_order_403_raises_a_rejection_carrying_the_broker_body(monkeypatch) -> None:
    """Regression: Alpaca's 403 (insufficient buying power) surfaced as a bare
    HTTPStatusError, the body with the reason was dropped and the whole tick failed."""
    body = '{"code":40310000,"message":"insufficient buying power"}'
    client = _client_answering(monkeypatch, 403, body)

    with pytest.raises(BrokerOrderRejectedError) as excinfo:
        client.submit_order({"symbol": "IWM", "side": "buy", "qty": "92"})

    assert excinfo.value.status_code == 403
    assert excinfo.value.body == body
    assert excinfo.value.symbol == "IWM"
    assert "insufficient buying power" in str(excinfo.value)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_submit_order_retryable_statuses_still_raise_http_errors(monkeypatch, status_code) -> None:
    client = _client_answering(monkeypatch, status_code, "busy")

    with pytest.raises(httpx.HTTPStatusError):
        client.submit_order({"symbol": "IWM"})


def test_submit_order_success_still_returns_the_payload(monkeypatch) -> None:
    client = _client_answering(monkeypatch, 200)

    assert client.submit_order({"symbol": "IWM"}) == {"id": "order-1"}
