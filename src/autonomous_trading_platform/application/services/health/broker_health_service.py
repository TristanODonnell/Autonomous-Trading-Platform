from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.detailed_health import (
    HealthCheckName,
    HealthCheckResult,
    HealthSeverity,
    HealthStatus,
    ServiceHealthReport,
)
from autonomous_trading_platform.storage.sor.repositories.queries.runtime_soak_verification_repository import (
    RuntimeSoakVerificationRepository,
)

_CONNECT_TIMEOUT = 3.0
_RECONCILIATION_STALE_THRESHOLD = timedelta(hours=4)


class BrokerAccountClient(Protocol):
    base_url: str

    def get_account(self) -> dict[str, Any]: ...
    def list_open_orders(self) -> list[dict[str, Any]]: ...


def _ok(name: HealthCheckName, message: str, metadata: dict[str, Any]) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.OK,
        severity=HealthSeverity.INFO,
        message=message,
        metadata=metadata,
    )


def _degraded(name: HealthCheckName, message: str, metadata: dict[str, Any]) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.DEGRADED,
        severity=HealthSeverity.WARNING,
        message=message,
        metadata=metadata,
    )


def _critical(name: HealthCheckName, message: str, metadata: dict[str, Any]) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.CRITICAL,
        severity=HealthSeverity.CRITICAL,
        message=message,
        metadata=metadata,
    )


def _derive_status(checks: list[HealthCheckResult]) -> HealthStatus:
    if any(c.status == HealthStatus.CRITICAL for c in checks):
        return HealthStatus.CRITICAL
    if any(c.status == HealthStatus.DEGRADED for c in checks):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


def _tcp_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


class BrokerHealthService:
    """
    TASK-613 — validates broker connectivity, auth, order endpoint health,
    and reconciliation freshness.

    Accepts any BrokerAccountClient-compatible object (AlpacaBrokerClient
    or any protocol implementation). Pass ``broker_client=None`` to skip
    live broker checks while still running the reconciliation freshness check.
    """

    def __init__(
        self,
        session: Session,
        *,
        broker_client: BrokerAccountClient | None = None,
        repository: RuntimeSoakVerificationRepository | None = None,
        reconciliation_stale_threshold: timedelta = _RECONCILIATION_STALE_THRESHOLD,
    ) -> None:
        self._session = session
        self._broker_client = broker_client
        self._repo = repository or RuntimeSoakVerificationRepository(session)
        self._reconciliation_stale_threshold = reconciliation_stale_threshold

    def check(self) -> ServiceHealthReport:
        now = datetime.now(UTC)
        checks = [
            self._check_connectivity(),
            self._check_auth(),
            self._check_order_endpoint(),
            self._check_reconciliation_freshness(now),
        ]
        return ServiceHealthReport(
            service="broker",
            status=_derive_status(checks),
            checks=checks,
        )

    def _check_connectivity(self) -> HealthCheckResult:
        if self._broker_client is None:
            return _degraded(
                HealthCheckName.BROKER_CONNECTIVITY,
                "Broker client not configured — connectivity check skipped.",
                {"broker_client_available": False},
            )

        base_url = getattr(self._broker_client, "base_url", "")
        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        metadata = {"base_url": base_url, "host": host, "port": port}

        if not host:
            return _degraded(
                HealthCheckName.BROKER_CONNECTIVITY,
                "Cannot determine broker hostname from base_url.",
                metadata,
            )

        if _tcp_reachable(host, port):
            return _ok(
                HealthCheckName.BROKER_CONNECTIVITY,
                f"Broker endpoint {host}:{port} is reachable.",
                metadata,
            )
        return _critical(
            HealthCheckName.BROKER_CONNECTIVITY,
            f"Broker endpoint {host}:{port} is not reachable — orders will fail.",
            metadata,
        )

    def _check_auth(self) -> HealthCheckResult:
        if self._broker_client is None:
            return _degraded(
                HealthCheckName.BROKER_AUTH,
                "Broker client not configured — auth check skipped.",
                {"broker_client_available": False},
            )

        try:
            account = self._broker_client.get_account()
            account_id = account.get("id", "")
            account_status = account.get("status", "")
            metadata = {
                "account_id": account_id,
                "account_status": account_status,
            }

            if not account_id:
                return _critical(
                    HealthCheckName.BROKER_AUTH,
                    "Broker returned an account without an ID — credentials may be invalid.",
                    metadata,
                )
            return _ok(
                HealthCheckName.BROKER_AUTH,
                f"Broker auth validated — account {account_id} is {account_status}.",
                metadata,
            )

        except Exception as exc:
            error = str(exc)
            status_code = None
            if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
                status_code = exc.response.status_code

            metadata = {"error": error, "status_code": status_code}

            if status_code in {401, 403}:
                return _critical(
                    HealthCheckName.BROKER_AUTH,
                    f"Broker rejected credentials with HTTP {status_code}.",
                    metadata,
                )
            return _degraded(
                HealthCheckName.BROKER_AUTH,
                f"Broker auth check failed: {error}",
                metadata,
            )

    def _check_order_endpoint(self) -> HealthCheckResult:
        if self._broker_client is None:
            return _degraded(
                HealthCheckName.BROKER_ORDER_ENDPOINT,
                "Broker client not configured — order endpoint check skipped.",
                {"broker_client_available": False},
            )

        try:
            orders = self._broker_client.list_open_orders()
            metadata: dict[str, Any] = {"open_order_count": len(orders)}
            return _ok(
                HealthCheckName.BROKER_ORDER_ENDPOINT,
                "Broker order endpoint responded successfully.",
                metadata,
            )
        except Exception as exc:
            error = str(exc)
            status_code = None
            if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
                status_code = exc.response.status_code
            metadata = {"error": error, "status_code": status_code}
            return _critical(
                HealthCheckName.BROKER_ORDER_ENDPOINT,
                f"Broker order endpoint failed: {error}",
                metadata,
            )

    def _check_reconciliation_freshness(self, now: datetime) -> HealthCheckResult:
        latest_broker_cash = self._repo.get_latest_broker_cash_snapshot()
        threshold_s = int(self._reconciliation_stale_threshold.total_seconds())

        if latest_broker_cash is None:
            return _degraded(
                HealthCheckName.BROKER_RECONCILIATION_FRESHNESS,
                "No broker reconciliation snapshot found — reconciliation has never run or data is missing.",
                {"threshold_seconds": threshold_s, "snapshot_found": False},
            )

        lag = max(0, int((now - latest_broker_cash.timestamp).total_seconds()))
        metadata: dict[str, Any] = {
            "threshold_seconds": threshold_s,
            "lag_seconds": lag,
            "snapshot_id": str(getattr(latest_broker_cash, "snapshot_id", None)),
            "snapshot_timestamp": latest_broker_cash.timestamp.isoformat(),
        }

        if lag > threshold_s:
            return _degraded(
                HealthCheckName.BROKER_RECONCILIATION_FRESHNESS,
                f"Last broker reconciliation was {lag}s ago (threshold {threshold_s}s) — reconciliation may be stalled.",
                metadata,
            )
        return _ok(
            HealthCheckName.BROKER_RECONCILIATION_FRESHNESS,
            f"Broker reconciliation is fresh: {lag}s since last snapshot.",
            metadata,
        )
