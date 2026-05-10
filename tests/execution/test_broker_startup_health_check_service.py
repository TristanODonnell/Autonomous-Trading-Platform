from typing import Any

import httpx
import pytest

from autonomous_trading_platform.execution.errors import (
    BrokerStartupHealthCheckError,
    InvalidBrokerCredentialsError,
)
from autonomous_trading_platform.execution.services.broker_startup_health_check_service import (
    BrokerStartupHealthCheckService,
)


class FakeBrokerClient:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result

    def get_account(self) -> dict[str, Any]:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/account")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("broker error", request=request, response=response)


def test_broker_startup_health_check_passes_with_account_id() -> None:
    result = BrokerStartupHealthCheckService().assert_broker_ready(
        FakeBrokerClient({"id": "paper-1"})
    )

    assert result.ok is True
    assert result.account_id == "paper-1"


def test_broker_startup_health_check_detects_invalid_credentials() -> None:
    with pytest.raises(InvalidBrokerCredentialsError, match="HTTP 401"):
        BrokerStartupHealthCheckService().assert_broker_ready(
            FakeBrokerClient(_http_status_error(401))
        )


def test_broker_startup_health_check_detects_forbidden_credentials() -> None:
    with pytest.raises(InvalidBrokerCredentialsError, match="HTTP 403"):
        BrokerStartupHealthCheckService().assert_broker_ready(
            FakeBrokerClient(_http_status_error(403))
        )


def test_broker_startup_health_check_fails_closed_for_broker_errors() -> None:
    with pytest.raises(BrokerStartupHealthCheckError, match="HTTP 500"):
        BrokerStartupHealthCheckService().assert_broker_ready(
            FakeBrokerClient(_http_status_error(500))
        )


def test_broker_startup_health_check_fails_closed_without_account_id() -> None:
    with pytest.raises(BrokerStartupHealthCheckError, match="no account id"):
        BrokerStartupHealthCheckService().assert_broker_ready(FakeBrokerClient({}))
