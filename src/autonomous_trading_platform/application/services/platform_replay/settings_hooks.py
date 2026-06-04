"""Settings domain replay hook."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.operator_settings_service import (
    OperatorSettingsService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    SettingsReplayResult,
    SettingsSummary,
    SettingsTimelineEvent,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


def _service(session: Session) -> OperatorSettingsService:
    return OperatorSettingsService(
        settings_repo=OperatorSettingsRepository(session),
        audit_log_repo=AuditLogRepository(session),
    )


def snapshot_settings_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> SettingsReplayResult:
    """Read current operator settings and compute a deterministic SHA256 hash."""
    base = dict(
        domain="settings",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    svc = _service(session)
    try:
        settings = svc.get_settings()
    except Exception as exc:
        return SettingsReplayResult(**base, status="failed", errors=[str(exc)])

    settings_dict = {
        k: str(v)
        for k, v in settings.__dict__.items()
        if not k.startswith("_") and k not in ("metadata",)
    }
    sha256 = hashlib.sha256(
        json.dumps(settings_dict, sort_keys=True, default=str).encode()
    ).hexdigest()

    settings_id = str(getattr(settings, "settings_id", ""))

    return SettingsReplayResult(
        **base,
        status="ok",
        settings_id=settings_id,
        sha256=sha256,
        summary={
            "settings_id": settings_id,
            "sha256": sha256,
            "risk_tolerance": getattr(settings, "risk_tolerance", None),
            "timestamp": timestamp.isoformat(),
        },
    )


def apply_settings_event(
    *,
    session: Session,
    timestamp: datetime,
    event: SettingsTimelineEvent,
    replay_context: PlatformReplayContext,
) -> SettingsReplayResult:
    """Apply a settings patch timeline event with actor/reason/audit trail."""
    base = dict(
        domain="settings",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if replay_context.dry_run:
        return SettingsReplayResult(
            **base,
            status="dry_run",
            event_applied=event.event_type,
            summary={"dry_run": True, "event_type": event.event_type, "patch": event.patch},
        )

    svc = _service(session)

    try:
        if event.event_type == "settings_reset_defaults":
            # Apply known safe defaults
            defaults: dict[str, Any] = {
                "risk_tolerance": "medium",
                "auto_promote_enabled": False,
                "auto_demote_on_breach": True,
                "notify_drawdown_alerts": True,
                "notify_strategy_promotion_events": True,
                "notify_pipeline_failures": True,
            }
            svc.update_settings(defaults, actor_user_id=event.actor, reason=event.reason)

        elif event.event_type in ("settings_changed", "settings_seeded"):
            if not event.patch:
                return SettingsReplayResult(
                    **base,
                    status="skipped",
                    warnings=["settings event had empty patch — skipped"],
                )
            svc.update_settings(event.patch, actor_user_id=event.actor, reason=event.reason)

        else:
            return SettingsReplayResult(
                **base,
                status="skipped",
                warnings=[f"Unhandled settings event type: {event.event_type}"],
            )

        session.commit()

    except Exception as exc:
        session.rollback()
        return SettingsReplayResult(**base, status="failed", errors=[str(exc)])

    # Re-read after write
    return snapshot_settings_at_timestamp(
        session=session,
        timestamp=timestamp,
        replay_context=replay_context,
    )


def build_settings_summary(*, session: Session) -> SettingsSummary:
    """Read current settings for the platform artifact bundle."""
    import hashlib
    import json

    svc = _service(session)
    try:
        settings = svc.get_settings()
    except Exception:
        return SettingsSummary(
            settings_id=None,
            sha256=None,
            risk_tolerance=None,
            max_drawdown_limit=None,
            auto_promote_enabled=None,
            rebalance_frequency=None,
        )

    settings_dict = {
        k: str(v)
        for k, v in settings.__dict__.items()
        if not k.startswith("_") and k not in ("metadata",)
    }
    sha256 = hashlib.sha256(
        json.dumps(settings_dict, sort_keys=True, default=str).encode()
    ).hexdigest()

    return SettingsSummary(
        settings_id=str(getattr(settings, "settings_id", "")),
        sha256=sha256,
        risk_tolerance=getattr(settings, "risk_tolerance", None),
        max_drawdown_limit=str(getattr(settings, "max_drawdown_limit", "")),
        auto_promote_enabled=getattr(settings, "auto_promote_enabled", None),
        rebalance_frequency=getattr(settings, "rebalance_frequency", None),
    )
