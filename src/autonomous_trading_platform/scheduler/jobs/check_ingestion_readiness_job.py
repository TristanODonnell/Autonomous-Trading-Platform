from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_window,
)


@dataclass(slots=True)
class IngestionReadinessResult:
    ready: bool
    safe_mode: bool
    reason: str | None = None


def check_ingestion_readiness_job(
    now_utc: datetime | None = None,
) -> IngestionReadinessResult:
    resolved_now = now_utc or datetime.now(UTC)
    cycle_window = build_trading_cycle_window(now_utc=resolved_now)

    if resolved_now > cycle_window.ingestion_deadline:
        return IngestionReadinessResult(
            ready=False,
            safe_mode=True,
            reason="ingestion_deadline_missed",
        )

    return IngestionReadinessResult(
        ready=True,
        safe_mode=False,
        reason=None,
    )
