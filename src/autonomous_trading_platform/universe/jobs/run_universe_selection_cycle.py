from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
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
    UniverseCandidateBuilder,
)
from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
)
from autonomous_trading_platform.universe.types import CandidateGenerationConfig


def should_rebalance(now_utc: datetime, cadence: str) -> bool:
    if cadence == "daily":
        return True
    if cadence == "weekly":
        return now_utc.weekday() == 0
    raise ValueError(f"Unsupported cadence: {cadence}")


def run_universe_selection_cycle(*, cycle_timestamp: datetime | None = None) -> None:
    settings = Settings()
    cadence = settings.universe_rebalance_cadence

    session: Session = get_session()

    now_utc = cycle_timestamp or datetime.now(UTC)
    if not should_rebalance(now_utc, cadence):
        return

    raw_pool_repo = RawMarketPoolRepository(session)
    raw_pool_query_service = RawMarketPoolQueryService(raw_pool_repo)
    builder = UniverseCandidateBuilder(session, raw_pool_query_service)
    version_repository = UniverseVersionRepository(session)
    validation_service = UniverseValidationService(session)

    config = CandidateGenerationConfig(as_of=now_utc)
    version_name = f"universe_{now_utc.date().isoformat()}"

    result = builder.build_candidate(
        config=config,
        name=version_name,
        rebalance_reason=cadence,
    )

    if not result.included_members:
        raise RuntimeError(
            "Universe candidate generation produced zero included symbols; "
            "refusing to create empty version"
        )

    version_repository.insert_version(result.version)
    version_repository.insert_members(result.included_members)

    validation = validation_service.validate_version_row(result.version, result.included_members)
    if not validation.ok:
        raise RuntimeError(validation.errors)

    version_repository.retire_active_version(result.version.effective_from)
    version_repository.activate_version(result.version.universe_version_id)
