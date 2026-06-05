"""Universe domain replay hook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    UniverseReplayResult,
    UniverseSummary,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
    UniverseRotationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.jobs.run_universe_rotation import (
    run_universe_rotation,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig


def run_universe_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    skip_cadence_check: bool = True,
    force_rotation: bool = False,
    dry_run: bool = False,
) -> UniverseReplayResult:
    """Run universe selection/rotation at timestamp T.

    Uses skip_cadence_check=True by default so the replay clock drives
    scheduling rather than wall-clock cadences.
    """
    base = dict(
        domain="universe",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if dry_run or replay_context.dry_run:
        version_repo = UniverseVersionRepository(session)
        active = version_repo.get_active_version(timestamp)
        members = version_repo.get_included_members(active.universe_version_id) if active else []
        return UniverseReplayResult(
            **base,
            status="dry_run",
            universe_version_id=active.universe_version_id if active else None,
            symbol_count=len(members),
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    warnings: list[str] = []
    try:
        result = run_universe_rotation(
            candidate_version_id=None,
            config=UniverseRebalanceConfig(),
            rotation_reason="platform_replay",
            force_rotation=force_rotation,
            approved_by=replay_context.actor,
            as_of=timestamp,
            skip_cadence_check=skip_cadence_check,
            dry_run=False,
        )
    except Exception as exc:
        return UniverseReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    if result is None:
        # Cadence check said skip — read current active version
        version_repo = UniverseVersionRepository(session)
        active = version_repo.get_active_version(timestamp)
        members = version_repo.get_included_members(active.universe_version_id) if active else []
        return UniverseReplayResult(
            **base,
            status="skipped",
            universe_version_id=active.universe_version_id if active else None,
            symbol_count=len(members),
            warnings=["Universe rotation skipped — cadence check"],
            summary={"skipped": True, "reason": "cadence_check"},
        )

    rec = result.rotation_record
    new_version = result.new_version
    version_repo2 = UniverseVersionRepository(session)
    members2 = version_repo2.get_included_members(new_version.universe_version_id)
    churn = float(rec.churn_pct) if rec.churn_pct is not None else None

    return UniverseReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        universe_version_id=new_version.universe_version_id,
        symbol_count=len(members2),
        rotation_applied=not result.skipped,
        churn_pct=churn,
        summary={
            "universe_version_id": new_version.universe_version_id,
            "symbol_count": len(members2),
            "rotation_applied": not result.skipped,
            "churn_pct": churn,
            "rotation_id": rec.rotation_id,
            "timestamp": timestamp.isoformat(),
        },
    )


def snapshot_universe_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> UniverseReplayResult:
    """Read-only: return active universe state without mutating."""
    base = dict(
        domain="universe",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )
    version_repo = UniverseVersionRepository(session)
    active = version_repo.get_active_version(timestamp)
    if active is None:
        return UniverseReplayResult(
            **base,
            status="skipped",
            warnings=["No active universe version found"],
        )
    members = version_repo.get_included_members(active.universe_version_id)
    return UniverseReplayResult(
        **base,
        status="ok",
        universe_version_id=active.universe_version_id,
        symbol_count=len(members),
        summary={
            "universe_version_id": active.universe_version_id,
            "symbol_count": len(members),
            "effective_from": active.effective_from.isoformat() if active.effective_from else None,
        },
    )


def build_universe_summary(*, session: Session, timestamp: datetime) -> UniverseSummary:
    """Read latest universe state for the platform artifact bundle."""
    version_repo = UniverseVersionRepository(session)
    rotation_repo = UniverseRotationRepository(session)

    active = version_repo.get_active_version(timestamp)
    members = version_repo.get_included_members(active.universe_version_id) if active else []
    latest_rotation = rotation_repo.get_latest()
    churn = (
        float(latest_rotation.churn_pct) if latest_rotation and latest_rotation.churn_pct else None
    )

    return UniverseSummary(
        active_version_id=active.universe_version_id if active else None,
        symbol_count=len(members),
        effective_from=active.effective_from.isoformat()
        if active and active.effective_from
        else None,
        rotation_count=0,
        last_churn_pct=churn,
    )
