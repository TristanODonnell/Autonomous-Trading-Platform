from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from autonomous_trading_platform.execution.services.order_execution_service import (
    OrderExecutionService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = dict(
        intent_id="intent-1",
        client_order_id="strat-AAPL-abc123",
        symbol="AAPL",
        side=SimpleNamespace(value="buy"),
    )
    data.update(overrides)
    return SimpleNamespace(**data)


class _FixedAdapter:
    def to_payload(self, intent: Any) -> dict[str, Any]:
        return {"symbol": intent.symbol, "client_order_id": intent.client_order_id}


class _PermissiveGuard:
    def assert_payload_allowed(self, _payload: Any) -> None:
        pass


def _make_service(client: Any, *, max_attempts: int = 3) -> OrderExecutionService:
    return OrderExecutionService(
        broker_client=client,
        adapter=_FixedAdapter(),
        paper_runtime_order_guard=_PermissiveGuard(),  # type: ignore[arg-type]
        max_attempts=max_attempts,
        initial_backoff_seconds=0,
    )


def _timeout() -> httpx.TransportError:
    return httpx.ReadTimeout("timed out")


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


_BROKER_ORDER = {"id": "broker-order-xyz", "status": "new", "client_order_id": "strat-AAPL-abc123"}


# ---------------------------------------------------------------------------
# Ambiguous failure → existing broker order found
# ---------------------------------------------------------------------------


class _TimeoutThenExistingOrderClient:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.lookup_calls = 0

    def submit_order(self, _payload: Any) -> dict[str, Any]:
        self.submit_calls += 1
        raise _timeout()

    def get_order_by_client_order_id(self, _client_order_id: str) -> dict[str, Any] | None:
        self.lookup_calls += 1
        return dict(_BROKER_ORDER)


def test_ambiguous_failure_existing_order_found_does_not_resubmit() -> None:
    client = _TimeoutThenExistingOrderClient()
    svc = _make_service(client)
    intent = _intent()

    result = svc.submit(intent)  # type: ignore[arg-type]

    assert result == _BROKER_ORDER
    assert client.submit_calls == 1, "must not resubmit after idempotency lookup succeeds"
    assert client.lookup_calls == 1


def test_ambiguous_failure_existing_order_returned_as_success() -> None:
    client = _TimeoutThenExistingOrderClient()
    svc = _make_service(client)
    result = svc.submit(_intent())  # type: ignore[arg-type]

    assert result["id"] == "broker-order-xyz"
    assert result["status"] == "new"


# ---------------------------------------------------------------------------
# Ambiguous failure → not found → retry succeeds
# ---------------------------------------------------------------------------


class _TimeoutThenNotFoundThenSuccessClient:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.lookup_calls = 0

    def submit_order(self, _payload: Any) -> dict[str, Any]:
        self.submit_calls += 1
        if self.submit_calls == 1:
            raise _timeout()
        return dict(_BROKER_ORDER)

    def get_order_by_client_order_id(self, _client_order_id: str) -> None:
        self.lookup_calls += 1
        return None


def test_ambiguous_failure_not_found_retries_and_succeeds() -> None:
    client = _TimeoutThenNotFoundThenSuccessClient()
    svc = _make_service(client)

    result = svc.submit(_intent())  # type: ignore[arg-type]

    assert result == _BROKER_ORDER
    assert client.submit_calls == 2, "must retry after lookup confirms order absent"
    assert client.lookup_calls == 1, "lookup must be called exactly once before retry"


# ---------------------------------------------------------------------------
# Idempotency lookup failure
# ---------------------------------------------------------------------------


class _TimeoutThenLookupFailsClient:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.lookup_calls = 0

    def submit_order(self, _payload: Any) -> dict[str, Any]:
        self.submit_calls += 1
        raise _timeout()

    def get_order_by_client_order_id(self, _client_order_id: str) -> dict[str, Any] | None:
        self.lookup_calls += 1
        raise httpx.ConnectError("lookup connection failed")


def test_lookup_failure_raises_and_does_not_resubmit() -> None:
    client = _TimeoutThenLookupFailsClient()
    svc = _make_service(client)

    with pytest.raises(httpx.ConnectError, match="lookup connection failed"):
        svc.submit(_intent())  # type: ignore[arg-type]

    assert client.submit_calls == 1, "must not resubmit when lookup fails"
    assert client.lookup_calls == 1


# ---------------------------------------------------------------------------
# Non-retryable HTTP failure (broker responded with error status)
# ---------------------------------------------------------------------------


class _HttpErrorClient:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code
        self.submit_calls = 0
        self.lookup_calls = 0

    def submit_order(self, _payload: Any) -> dict[str, Any]:
        self.submit_calls += 1
        raise _http_error(self._status_code)

    def get_order_by_client_order_id(self, _client_order_id: str) -> dict[str, Any] | None:
        self.lookup_calls += 1
        return None


def test_http_error_does_not_trigger_idempotency_lookup() -> None:
    # 422 = broker validation failure — broker definitively received and rejected the request
    client = _HttpErrorClient(422)
    svc = _make_service(client, max_attempts=1)

    with pytest.raises(httpx.HTTPStatusError):
        svc.submit(_intent())  # type: ignore[arg-type]

    assert client.lookup_calls == 0, "idempotency lookup must not be triggered for HTTP errors"


def test_http_error_not_retried_after_max_attempts_exceeded() -> None:
    client = _HttpErrorClient(500)
    svc = _make_service(client, max_attempts=1)

    with pytest.raises(httpx.HTTPStatusError):
        svc.submit(_intent())  # type: ignore[arg-type]

    assert client.submit_calls == 1


# ---------------------------------------------------------------------------
# Retry exhaustion
# ---------------------------------------------------------------------------


class _AlwaysTimeoutNeverFoundClient:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.lookup_calls = 0

    def submit_order(self, _payload: Any) -> dict[str, Any]:
        self.submit_calls += 1
        raise _timeout()

    def get_order_by_client_order_id(self, _client_order_id: str) -> None:
        self.lookup_calls += 1
        return None


def test_retry_exhaustion_raises_after_max_attempts() -> None:
    client = _AlwaysTimeoutNeverFoundClient()
    svc = _make_service(client, max_attempts=2)

    with pytest.raises(httpx.TransportError):
        svc.submit(_intent())  # type: ignore[arg-type]

    # attempt 1: submit fails (ambiguous) → lookup (not found) → retry
    # attempt 2: submit fails → max_attempts reached → raise
    assert client.submit_calls == 2
    assert client.lookup_calls == 1


def test_retry_exhaustion_records_no_successful_order() -> None:
    client = _AlwaysTimeoutNeverFoundClient()
    svc = _make_service(client, max_attempts=3)

    with pytest.raises(httpx.TransportError):
        svc.submit(_intent())  # type: ignore[arg-type]

    # attempt 1: fail → lookup (not found) → retry
    # attempt 2: fail → lookup (not found) → retry
    # attempt 3: fail → max_attempts → raise (no final lookup)
    assert client.submit_calls == 3
    assert client.lookup_calls == 2


# ---------------------------------------------------------------------------
# client_order_id stability across retries
# ---------------------------------------------------------------------------


class _CapturingClient:
    def __init__(self) -> None:
        self.submitted_payloads: list[dict[str, Any]] = []
        self.submit_calls = 0

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submit_calls += 1
        self.submitted_payloads.append(dict(payload))
        if self.submit_calls == 1:
            raise _timeout()
        return dict(_BROKER_ORDER)

    def get_order_by_client_order_id(self, _client_order_id: str) -> None:
        return None


def test_client_order_id_is_stable_across_retries() -> None:
    client = _CapturingClient()
    svc = _make_service(client)

    svc.submit(_intent())  # type: ignore[arg-type]

    assert len(client.submitted_payloads) == 2
    ids = {p["client_order_id"] for p in client.submitted_payloads}
    assert ids == {"strat-AAPL-abc123"}, "client_order_id must not change between retry attempts"


def test_payload_is_not_regenerated_on_retry() -> None:
    client = _CapturingClient()
    svc = _make_service(client)

    svc.submit(_intent())  # type: ignore[arg-type]

    first, second = client.submitted_payloads
    assert first == second, "identical payload must be sent on every attempt"
