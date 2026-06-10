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

    # ── Step 1: refresh raw market pool via screener ─────────────────────────
    # Point-in-time correct: ranks all active US equities by dollar volume as
    # of this rotation date, so the candidate pool reflects that month's market.
    try:
        from autonomous_trading_platform.storage.sor.repositories.core.raw_market_pool_repository import (
            RawMarketPoolRepository,
        )
        from autonomous_trading_platform.universe.providers.alpaca_screener_provider import (
            AlpacaScreenerProvider,
        )
        from autonomous_trading_platform.universe.services.raw_market_pool_refresh_service import (
            RawMarketPoolRefreshService,
        )

        screener = AlpacaScreenerProvider(as_of=timestamp.date(), top_n=500)
        pool_repo = RawMarketPoolRepository(session)
        refresh_svc = RawMarketPoolRefreshService(pool_repo, screener)
        refresh_svc.refresh(cadence="monthly", captured_at=timestamp)
        session.commit()
    except Exception as exc:
        warnings.append(f"screener_refresh_failed: {exc}")

    # ── Step 2: generate scored candidate universe from pool ─────────────────
    try:
        from autonomous_trading_platform.universe.jobs.run_candidate_generation import (
            run_candidate_generation,
        )
        from autonomous_trading_platform.universe.types import CandidateGenerationConfig

        run_candidate_generation(
            as_of=timestamp,
            config=CandidateGenerationConfig(as_of=timestamp, lookback_days=20, max_symbols=500),
            rebalance_reason="platform_replay_monthly",
            dataset_version_id=replay_context.dataset_version_id,
        )
    except Exception as exc:
        warnings.append(f"candidate_generation_failed: {exc}")

    # ── Step 3: rotate — select best 20 from scored candidates ──────────────
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
            warnings=warnings,
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
