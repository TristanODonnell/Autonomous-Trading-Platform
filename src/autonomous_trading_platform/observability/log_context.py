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

    def to_extra(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}
