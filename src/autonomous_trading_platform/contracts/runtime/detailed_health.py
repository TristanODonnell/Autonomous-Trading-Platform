from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class HealthSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class HealthCheckName(StrEnum):
    # OTEL
    OTEL_COLLECTOR_REACHABLE = "otel_collector_reachable"
    OTEL_METRIC_EXPORTER = "otel_metric_exporter"
    OTEL_TRACE_EXPORTER = "otel_trace_exporter"
    OTEL_LOG_EXPORTER = "otel_log_exporter"
    # Jobs
    JOB_STALE = "job_stale"
    JOB_DEAD = "job_dead"
    JOB_HUNG = "job_hung"
    JOB_DUPLICATE_RUNNING = "job_duplicate_running"
    JOB_ORPHANED = "job_orphaned"
    # Data pipeline
    DATA_RAW_BARS_FRESHNESS = "data_raw_bars_freshness"
    DATA_FEATURE_FRESHNESS = "data_feature_freshness"
    DATA_MANIFEST_FRESHNESS = "data_manifest_freshness"
    DATA_INGESTION_LAG = "data_ingestion_lag"
    DATA_FEATURE_LAG = "data_feature_lag"
    # Broker
    BROKER_CONNECTIVITY = "broker_connectivity"
    BROKER_AUTH = "broker_auth"
    BROKER_ORDER_ENDPOINT = "broker_order_endpoint"
    BROKER_RECONCILIATION_FRESHNESS = "broker_reconciliation_freshness"
    # Control state
    CONTROL_KILL_SWITCH = "control_kill_switch"
    CONTROL_PAUSE_STATE = "control_pause_state"
    CONTROL_FREEZE_STATE = "control_freeze_state"
    CONTROL_DEGRADATION_STATE = "control_degradation_state"


class HealthCheckResult(BaseModel):
    check_name: HealthCheckName
    status: HealthStatus
    severity: HealthSeverity
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceHealthReport(BaseModel):
    service: str
    status: HealthStatus
    checks: list[HealthCheckResult]


class DetailedSystemHealthReport(BaseModel):
    status: HealthStatus
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    environment: str
    services: list[ServiceHealthReport]
    summary: dict[str, int] = Field(default_factory=dict)
