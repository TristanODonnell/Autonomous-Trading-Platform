"""Controls domain replay hook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.application.services.strategy_control_service import (
    StrategyControlService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    ControlsReplayResult,
    ControlsSummary,
    ControlsTimelineEvent,
    PlatformReplayContext,
)
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


def snapshot_controls_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> ControlsReplayResult:
    """Read all control state — used by the platform runner before each trading cycle."""
    base = dict(
        domain="controls",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )
    repo = RuntimeControlStateRepository(session)
    state = repo.get_global_state()

    trading_paused = bool(state.trading_paused) if state else False
    kill_switch = bool(state.kill_switch_enabled) if state else False
    trading_mode = state.trading_mode if state else None

    return ControlsReplayResult(
        **base,
        status="ok",
        trading_paused=trading_paused,
        kill_switch_active=kill_switch,
        trading_mode=trading_mode,
        summary={
            "trading_paused": trading_paused,
            "kill_switch_active": kill_switch,
            "trading_mode": trading_mode,
            "timestamp": timestamp.isoformat(),
        },
    )


def apply_controls_event(
    *,
    session: Session,
    timestamp: datetime,
    event: ControlsTimelineEvent,
    replay_context: PlatformReplayContext,
) -> ControlsReplayResult:
    """Apply a controls timeline event with actor/reason/audit trail."""
    base = dict(
        domain="controls",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if replay_context.dry_run:
        return ControlsReplayResult(
            **base,
            status="dry_run",
            event_applied=event.event_type,
            summary={"dry_run": True, "event_type": event.event_type},
        )

    svc = RuntimeControlService(session=session)
    strategy_svc = StrategyControlService(session=session)
    alloc_svc = StrategyAllocationService(session=session)

    try:
        if event.event_type == "controls_paused":
            svc.pause_trading(updated_by=event.actor, rationale=event.reason)

        elif event.event_type == "controls_resumed":
            svc.resume_trading(updated_by=event.actor, rationale=event.reason)

        elif event.event_type == "trading_enabled":
            svc.set_trading_enabled(enabled=True, updated_by=event.actor, rationale=event.reason)

        elif event.event_type == "trading_disabled":
            svc.set_trading_enabled(enabled=False, updated_by=event.actor, rationale=event.reason)

        elif event.event_type == "trading_mode_changed":
            if not event.trading_mode:
                raise ValueError("trading_mode_changed requires trading_mode")
            svc.update_trading_mode(
                updated_by=event.actor,
                mode=event.trading_mode,
                rationale=event.reason,
            )

        elif event.event_type == "strategy_disabled":
            if not event.strategy_id:
                raise ValueError("strategy_disabled requires strategy_id")
            strategy_svc.set_enabled(
                strategy_id=event.strategy_id,
                enabled=False,
                reason=event.reason,
                updated_by=event.actor,
            )

        elif event.event_type == "strategy_enabled":
            if not event.strategy_id:
                raise ValueError("strategy_enabled requires strategy_id")
            strategy_svc.set_enabled(
                strategy_id=event.strategy_id,
                enabled=True,
                reason=event.reason,
                updated_by=event.actor,
            )

        elif event.event_type == "allocation_override_set":
            if not event.strategy_id or event.allocation_pct is None:
                raise ValueError("allocation_override_set requires strategy_id and allocation_pct")
            alloc_svc.override_allocation(
                strategy_id=event.strategy_id,
                allocation_pct=event.allocation_pct,
                reason=event.reason,
                updated_by=event.actor,
            )

        elif event.event_type == "allocation_override_cleared":
            if not event.strategy_id:
                raise ValueError("allocation_override_cleared requires strategy_id")
            alloc_svc.clear_allocation_override(
                strategy_id=event.strategy_id,
                reason=event.reason,
                updated_by=event.actor,
            )

        else:
            return ControlsReplayResult(
                **base,
                status="skipped",
                warnings=[f"Unhandled controls event type: {event.event_type}"],
            )

    except Exception as exc:
        session.rollback()
        return ControlsReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    ctrl_state = RuntimeControlStateRepository(session).get_global_state()
    return ControlsReplayResult(
        **base,
        status="ok",
        trading_paused=bool(ctrl_state.trading_paused) if ctrl_state else False,
        kill_switch_active=bool(ctrl_state.kill_switch_enabled) if ctrl_state else False,
        trading_mode=ctrl_state.trading_mode if ctrl_state else None,
        event_applied=event.event_type,
        summary={"event_type": event.event_type, "actor": event.actor},
    )


def build_controls_summary(*, session: Session) -> ControlsSummary:
    """Read current controls state for the platform artifact bundle."""
    ctrl_repo = RuntimeControlStateRepository(session)
    state = ctrl_repo.get_global_state()

    all_strategy_states = list(session.scalars(select(StrategyControlState)).all())
    disabled = [s.strategy_id for s in all_strategy_states if not s.enabled]

    override_repo = AllocationOverridesRepository(session)
    active_overrides = len(override_repo.get_all_active())

    return ControlsSummary(
        trading_paused=bool(state.trading_paused) if state else False,
        kill_switch_active=bool(state.kill_switch_enabled) if state else False,
        trading_mode=state.trading_mode if state else None,
        strategy_count=len(all_strategy_states),
        disabled_strategies=disabled,
        active_overrides=active_overrides,
    )
