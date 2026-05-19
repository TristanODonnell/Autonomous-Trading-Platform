from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.universe.services.universe_rotation_service import (
    RotationResult,
    UniverseRotationService,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig


def should_rotate(now_utc: datetime, cadence: str) -> bool:
    if cadence == "daily":
        return True
    if cadence == "weekly":
        return now_utc.weekday() == 0
    raise ValueError(f"Unsupported cadence: {cadence!r}")


def run_universe_rotation(
    *,
    candidate_version_id: str | None = None,
    config: UniverseRebalanceConfig | None = None,
    rotation_reason: str | None = None,
    force_rotation: bool = False,
    approved_by: str | None = None,
    as_of: datetime | None = None,
    skip_cadence_check: bool = False,
    dry_run: bool = False,
) -> RotationResult | None:
    """
    Run the scheduled universe rotation lifecycle.

    1. Checks if rotation is due based on configured cadence (unless skipped).
    2. Resolves latest candidate and current active universe.
    3. Proposes a rebalanced universe via UniverseRebalanceService.
    4. Validates pre-activation safety rules.
    5. Atomically retires previous and activates new universe version.
    6. Persists a rotation audit record.

    Returns the RotationResult, or None if skipped due to cadence.
    In dry_run mode the transaction is rolled back after computing the result.
    """
    settings = Settings()
    now_utc = _ensure_utc(as_of or datetime.now(UTC))

    if not skip_cadence_check and not should_rotate(now_utc, settings.universe_rebalance_cadence):
        return None

    if config is None:
        config = UniverseRebalanceConfig()

    effective_reason = rotation_reason or settings.universe_rebalance_cadence

    session: Session = get_session()
    try:
        svc = UniverseRotationService(session)
        result = svc.rotate(
            candidate_version_id=candidate_version_id,
            config=config,
            rotation_reason=effective_reason,
            force_rotation=force_rotation,
            approved_by=approved_by,
            as_of=now_utc,
        )

        if dry_run:
            session.rollback()
        else:
            session.commit()

        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
