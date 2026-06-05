"""Runtime (trading cycle) domain replay hook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    TradingCycleReplayResult,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


def run_trading_cycle_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> TradingCycleReplayResult:
    """Run one trading cycle at timestamp T.

    This is the core clock tick of the platform replay runner.
    """
    base = dict(
        domain="runtime",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    # Read controls state to detect pre-existing blocks
    ctrl_state = RuntimeControlStateRepository(session).get_global_state()
    if ctrl_state is not None:
        if ctrl_state.kill_switch_enabled:
            return TradingCycleReplayResult(
                **base,
                status="skipped",
                cycle_blocked=True,
                block_reason="kill_switch_enabled",
                warnings=["Trading cycle skipped — kill switch enabled"],
            )
        if not ctrl_state.trading_enabled:
            return TradingCycleReplayResult(
                **base,
                status="skipped",
                cycle_blocked=True,
                block_reason="trading_disabled",
                warnings=["Trading cycle skipped — trading disabled"],
            )
        if ctrl_state.trading_paused:
            return TradingCycleReplayResult(
                **base,
                status="skipped",
                cycle_blocked=True,
                block_reason="trading_paused",
                warnings=["Trading cycle skipped — trading paused"],
            )

    if dry_run or replay_context.dry_run:
        return TradingCycleReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    try:
        run_trading_cycle(now_utc=timestamp)
    except Exception as exc:
        return TradingCycleReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    return TradingCycleReplayResult(
        **base,
        status="ok",
        summary={"timestamp": timestamp.isoformat()},
    )
