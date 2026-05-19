from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.universe.services.universe_rotation_service import (
    RotationResult,
    UniverseRotationService,
)


def run_universe_rollback(
    *,
    target_version_id: str,
    rollback_reason: str,
    approved_by: str | None = None,
    as_of: datetime | None = None,
    dry_run: bool = False,
) -> RotationResult:
    """
    Revert the active universe to a prior version.

    Creates a new PROPOSED version from the target's members, retires the
    current ACTIVE version, and activates the copy. A rollback rotation record
    is persisted for the audit trail.

    In dry_run mode the transaction is rolled back after computing the result.
    """
    now_utc = _ensure_utc(as_of or datetime.now(UTC))

    session: Session = get_session()
    try:
        svc = UniverseRotationService(session)
        result = svc.rollback(
            target_version_id=target_version_id,
            rollback_reason=rollback_reason,
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
