from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.universe_rebalance_repository import (
    UniverseRebalanceRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.universe_rebalance_service import (
    RebalanceResult,
    UniverseRebalanceService,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig


def run_rebalance(
    *,
    candidate_version_id: str | None = None,
    active_version_id: str | None = None,
    config: UniverseRebalanceConfig | None = None,
    name: str | None = None,
    as_of: datetime | None = None,
    dry_run: bool = False,
) -> RebalanceResult:
    """
    Propose a rebalanced universe from the latest (or specified) candidate.

    Resolves the active and candidate versions when not provided explicitly.
    Persists the proposed universe version, its members, and the rebalance run
    record unless dry_run is True.

    Returns the RebalanceResult for inspection.
    """
    session: Session = get_session()

    now_utc = as_of or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    if config is None:
        config = UniverseRebalanceConfig()

    version_repo = UniverseVersionRepository(session)
    rebalance_repo = UniverseRebalanceRepository(session)

    if candidate_version_id is None:
        candidates = version_repo.get_candidate_versions(limit=1)
        if not candidates:
            raise RuntimeError(
                "No candidate universe versions found. Run candidate-generate first."
            )
        candidate_version_id = candidates[0].universe_version_id

    if active_version_id is None:
        active = version_repo.get_active_version(now_utc)
        active_version_id = active.universe_version_id if active else None

    service = UniverseRebalanceService(session)
    result = service.propose_rebalance(
        active_version_id=active_version_id,
        candidate_version_id=candidate_version_id,
        config=config,
        name=name,
    )

    if not dry_run:
        version_repo.insert_version(result.proposed_version)
        if result.proposed_members:
            version_repo.insert_members(result.proposed_members)
        rebalance_repo.insert_run(result.rebalance_run)
        session.commit()

    return result
