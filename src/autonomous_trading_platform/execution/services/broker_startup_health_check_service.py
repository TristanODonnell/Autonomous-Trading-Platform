from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from autonomous_trading_platform.execution.errors import (
    BrokerStartupHealthCheckError,
    InvalidBrokerCredentialsError,
)


class BrokerAccountClient(Protocol):
    def get_account(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class BrokerStartupHealthCheckResult:
    ok: bool
    account_id: str


class BrokerStartupHealthCheckService:
    """
    Validate that the broker client is usable before order-sensitive runtime work.
    """

    def assert_broker_ready(
        self, broker_client: BrokerAccountClient
    ) -> BrokerStartupHealthCheckResult:
        try:
            account = broker_client.get_account()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                raise InvalidBrokerCredentialsError(
                    f"Broker credentials rejected with HTTP {status_code}."
                ) from exc
            raise BrokerStartupHealthCheckError(
                f"Broker startup health check failed with HTTP {status_code}."
            ) from exc
        except Exception as exc:
            raise BrokerStartupHealthCheckError(
                f"Broker startup health check failed: {exc}"
            ) from exc

        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise BrokerStartupHealthCheckError(
                "Broker startup health check returned no account id."
            )

        return BrokerStartupHealthCheckResult(ok=True, account_id=account_id)
