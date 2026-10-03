"""Seed point-in-time universe versions the way rotation does (retire previous, activate new)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.universe_version_service import (
    UniverseVersionService,
)


def seed_universe_version(
    session: Session,
    *,
    symbols: list[str],
    effective_from: datetime,
    name: str | None = None,
) -> str:
    """Retire the current active version at effective_from and activate a new one.

    Returns the new universe_version_id.
    """
    repo = UniverseVersionRepository(session)
    repo.retire_active_version(effective_from)
    session.flush()
    version, members = UniverseVersionService(repo).build_version(
        name=name or f"seed_{effective_from.date().isoformat()}",
        effective_from=effective_from,
        symbols=symbols,
        source="custom",
        rebalance_reason="test_seed",
    )
    repo.insert_version(version)
    session.flush()
    repo.insert_members(members)
    session.flush()
    repo.activate_version(version.universe_version_id)
    session.flush()
    return str(version.universe_version_id)
