from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class LogContext:
    run_id: str | None = None
    strategy_id: str | None = None
    symbol: str | None = None
    bar_timestamp: datetime | str | None = None
    cycle_timestamp: datetime | str | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    order_intent_id: str | None = None
    incident_type: str | None = None
    component: str | None = None
    job: str | None = None
    step: str | None = None
    duration_seconds: float | None = None
    raw_action_count: int | None = None
    request_duration: float | None = None
    exception_type: str | None = None
    error_message: str | None = None
    failure_class: str | None = None
    correlation_id: str | None = None
    parent_job_run_id: str | None = None
    reconciliation_status: str | None = None
    broker_order_id: str | None = None
    fill_id: str | None = None
    check_name: str | None = None
    check_status: str | None = None
    expected_cash: float | None = None
    actual_cash: float | None = None
    cash_drift: float | None = None
    expected_position_qty: float | None = None
    actual_position_qty: float | None = None
    position_drift: float | None = None
    soak_window_hours: float | None = None
    severity: str | None = None
    broker_endpoint: str | None = None
    status_code: int | None = None

    def to_extra(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}
