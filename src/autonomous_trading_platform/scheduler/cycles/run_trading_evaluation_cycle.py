from datetime import UTC, datetime

from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_dependencies,
    build_trading_cycle_window,
    build_trading_run_manifest,
    new_trading_run_id,
)
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    run_trading_evaluation_job,
)


def run_trading_evaluation_cycle(
    *,
    timestamp: datetime | None = None,
):
    resolved_now_utc = timestamp or datetime.now(UTC)
    trading_cycle_window = build_trading_cycle_window(now_utc=resolved_now_utc)
    run_id = new_trading_run_id()

    deps = build_trading_cycle_dependencies()
    manifest = build_trading_run_manifest(
        run_id=run_id,
        now_utc=resolved_now_utc,
        cycle_start=trading_cycle_window.cycle_start,
        cycle_end=trading_cycle_window.cycle_end,
        strategy_id=deps.active_strategy_id,
        governance_state=deps.active_governance_state,
    )

    try:
        return run_trading_evaluation_job(
            now_utc=resolved_now_utc,
            trading_cycle_dependencies=deps,
            manifest=manifest,
        )
    finally:
        deps.session.close()
