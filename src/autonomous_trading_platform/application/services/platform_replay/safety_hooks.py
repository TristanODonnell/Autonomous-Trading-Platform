"""Safety domain replay hook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    SafetyReplayResult,
    SafetySummary,
    SafetyTimelineEvent,
)
from autonomous_trading_platform.storage.sor.models.kill_switch_state import (
    KILL_SWITCH_SINGLETON_ID,
    KillSwitchState,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


def snapshot_safety_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> SafetyReplayResult:
    """Read kill-switch and gate state — no mutations."""
    base = dict(
        domain="safety",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    ks_state = session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)
    kill_switch_enabled = bool(ks_state and ks_state.is_enabled)

    ctrl_state = RuntimeControlStateRepository(session).get_global_state()
    trading_enabled = bool(ctrl_state.trading_enabled) if ctrl_state else True

    gate_ok = not kill_switch_enabled and trading_enabled

    return SafetyReplayResult(
        **base,
        status="ok",
        kill_switch_enabled=kill_switch_enabled,
        gate_ok=gate_ok,
        summary={
            "kill_switch_enabled": kill_switch_enabled,
            "gate_ok": gate_ok,
            "timestamp": timestamp.isoformat(),
        },
    )


def apply_safety_event(
    *,
    session: Session,
    timestamp: datetime,
    event: SafetyTimelineEvent,
    replay_context: PlatformReplayContext,
) -> SafetyReplayResult:
    """Apply a safety timeline event (halt/release/arm/disarm)."""
    base = dict(
        domain="safety",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if replay_context.dry_run:
        return SafetyReplayResult(
            **base,
            status="dry_run",
            event_applied=event.event_type,
            summary={"dry_run": True, "event_type": event.event_type},
        )

    svc = RuntimeControlService(session=session)

    try:
        if event.event_type == "safety_emergency_halt":
            svc.activate_kill_switch(
                triggered_by=event.actor,
                reason=event.reason,
            )

        elif event.event_type == "safety_release_halt":
            svc.resume_trading(
                updated_by=event.actor,
                rationale=event.reason,
            )

        elif event.event_type == "safety_startup_check":
            ks_repo = KillSwitchStateRepository(session=session)
            audit_repo = AuditLogRepository(session=session)
            from autonomous_trading_platform.safety.services.kill_switch_service import (
                KillSwitchService,
            )

            ks_svc = KillSwitchService(
                repository=ks_repo,
                runtime_control_repo=RuntimeControlStateRepository(session=session),
            )
            ks_svc.emit_startup_audit_event(audit_repo=audit_repo)

        elif event.event_type in ("live_trading_armed", "live_trading_disarmed"):
            # Runtime gate is process-local — record in audit log only
            audit_repo = AuditLogRepository(session=session)
            import uuid

            from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent

            audit_repo.add(
                AuditLogEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=str(replay_context.run_id),
                    event_type=event.event_type.upper(),
                    component="safety",
                    event_timestamp=timestamp,
                    message=event.reason,
                    metadata={"actor": event.actor},
                )
            )

        else:
            return SafetyReplayResult(
                **base,
                status="skipped",
                warnings=[f"Unhandled safety event type: {event.event_type}"],
            )

    except Exception as exc:
        session.rollback()
        return SafetyReplayResult(**base, status="failed", errors=[str(exc)])

    return snapshot_safety_at_timestamp(
        session=session,
        timestamp=timestamp,
        replay_context=replay_context,
    )


def build_safety_summary(*, session: Session) -> SafetySummary:
    """Read current safety state for the platform artifact bundle."""
    import contextlib

    from autonomous_trading_platform.config.settings import Settings

    ks_state = session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)
    kill_switch_enabled = bool(ks_state and ks_state.is_enabled)

    trading_env: str | None = None
    with contextlib.suppress(Exception):
        trading_env = Settings().trading_environment.value

    return SafetySummary(
        kill_switch_enabled=kill_switch_enabled,
        live_gate_armed=False,  # process-local gate is not persisted
        trading_environment=trading_env,
        recent_safety_events=[],
    )
