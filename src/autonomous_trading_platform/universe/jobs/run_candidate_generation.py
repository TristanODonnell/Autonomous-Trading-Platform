from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import UniverseSource
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.raw_market_pool_repository import (
    RawMarketPoolRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.storage.sor.services.raw_market_pool_query_service import (
    RawMarketPoolQueryService,
)
from autonomous_trading_platform.universe.services.universe_candidate_builder import (
    CandidateBuildResult,
    UniverseCandidateBuilder,
)
from autonomous_trading_platform.universe.types import CandidateGenerationConfig


def run_candidate_generation(
    *,
    as_of: datetime | None = None,
    config: CandidateGenerationConfig | None = None,
    name: str | None = None,
    source: str = UniverseSource.CUSTOM,
    rebalance_reason: str | None = None,
) -> CandidateBuildResult:
    """
    Generate and persist a candidate universe version.

    Persists both included and excluded UniverseMember rows for full
    diagnostic traceability. Does NOT activate the version.

    Returns the CandidateBuildResult for inspection by callers.
    """
    session: Session = get_session()

    now_utc = as_of or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    if config is None:
        config = CandidateGenerationConfig(as_of=now_utc)

    if name is None:
        name = f"candidate_{config.as_of.date().isoformat()}"

    raw_pool_repo = RawMarketPoolRepository(session)
    raw_pool_query_service = RawMarketPoolQueryService(raw_pool_repo)
    builder = UniverseCandidateBuilder(session, raw_pool_query_service)
    version_repository = UniverseVersionRepository(session)

    result = builder.build_candidate(
        config=config,
        name=name,
        source=source,
        rebalance_reason=rebalance_reason,
    )

    version_repository.insert_version(result.version)
    all_members = result.included_members + result.excluded_members
    if all_members:
        version_repository.insert_members(all_members)

    session.commit()
    return result
