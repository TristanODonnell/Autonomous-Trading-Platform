from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

from autonomous_trading_platform.contracts.runtime.detailed_health import (
    HealthCheckName,
    HealthCheckResult,
    HealthSeverity,
    HealthStatus,
    ServiceHealthReport,
)

_CONNECT_TIMEOUT = 2.0


def _tcp_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _ok(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.OK,
        severity=HealthSeverity.INFO,
        message=message,
        metadata=metadata,
    )


def _degraded(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.DEGRADED,
        severity=HealthSeverity.WARNING,
        message=message,
        metadata=metadata,
    )


def _critical(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
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


class OtelHealthService:
    """
    TASK-610 — validates OTEL exporter connectivity and provider configuration.

    Checks:
    - collector gRPC endpoint reachable (TCP)
    - metric provider is SDK (not no-op)
    - trace provider is SDK (not no-op)
    - log exporter HTTP endpoint reachable (TCP)
    """

    def __init__(self) -> None:
        self._grpc_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        self._http_logs_endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
            "http://localhost:4318/v1/logs",
        )

    def check(self) -> ServiceHealthReport:
        checks = [
            self._check_collector_reachable(),
            self._check_metric_exporter(),
            self._check_trace_exporter(),
            self._check_log_exporter(),
        ]
        return ServiceHealthReport(
            service="otel",
            status=_derive_status(checks),
            checks=checks,
        )

    def _check_collector_reachable(self) -> HealthCheckResult:
        parsed = urlparse(self._grpc_endpoint)
        host = parsed.hostname or "localhost"
        port = parsed.port or 4317
        metadata = {"endpoint": self._grpc_endpoint, "host": host, "port": port}

        if _tcp_reachable(host, port):
            return _ok(
                HealthCheckName.OTEL_COLLECTOR_REACHABLE,
                "OTEL collector gRPC endpoint is reachable.",
                metadata,
            )
        return _critical(
            HealthCheckName.OTEL_COLLECTOR_REACHABLE,
            f"OTEL collector at {host}:{port} is not reachable — spans and metrics will be dropped.",
            metadata,
        )

    def _check_metric_exporter(self) -> HealthCheckResult:
        provider = metrics.get_meter_provider()
        is_sdk = isinstance(provider, SDKMeterProvider)
        metadata = {
            "provider_type": type(provider).__name__,
            "endpoint": self._grpc_endpoint,
            "sdk_configured": is_sdk,
        }

        if is_sdk:
            return _ok(
                HealthCheckName.OTEL_METRIC_EXPORTER,
                "Metric exporter is configured (SDK MeterProvider active).",
                metadata,
            )
        return _degraded(
            HealthCheckName.OTEL_METRIC_EXPORTER,
            "Metric exporter is not configured — no-op MeterProvider detected. Call setup_telemetry() at process startup.",
            metadata,
        )

    def _check_trace_exporter(self) -> HealthCheckResult:
        provider = trace.get_tracer_provider()
        is_sdk = isinstance(provider, SDKTracerProvider)
        metadata = {
            "provider_type": type(provider).__name__,
            "endpoint": self._grpc_endpoint,
            "sdk_configured": is_sdk,
        }

        if is_sdk:
            return _ok(
                HealthCheckName.OTEL_TRACE_EXPORTER,
                "Trace exporter is configured (SDK TracerProvider active).",
                metadata,
            )
        return _degraded(
            HealthCheckName.OTEL_TRACE_EXPORTER,
            "Trace exporter is not configured — no-op TracerProvider detected. Call setup_telemetry() at process startup.",
            metadata,
        )

    def _check_log_exporter(self) -> HealthCheckResult:
        parsed = urlparse(self._http_logs_endpoint)
        host = parsed.hostname or "localhost"
        port = parsed.port or 4318
        metadata = {"endpoint": self._http_logs_endpoint, "host": host, "port": port}

        if _tcp_reachable(host, port):
            return _ok(
                HealthCheckName.OTEL_LOG_EXPORTER,
                "OTEL log exporter HTTP endpoint is reachable.",
                metadata,
            )
        return _degraded(
            HealthCheckName.OTEL_LOG_EXPORTER,
            f"OTEL log exporter endpoint at {host}:{port} is not reachable — log records will be dropped.",
            metadata,
        )
