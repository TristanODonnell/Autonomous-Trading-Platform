# autonomous_trading_platform/strategy/contexts/build_strategy_runtime_context.py

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.runtime.services.live_bar_dataset_resolver import (
    LiveBarDatasetResolver,
)
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.parquet.datasets import (
    RAW_BARS_DATASET,
    ParquetDataset,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.sor.repositories.core.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.storage.sor.services.corporate_action_split_source import (
    SorSplitSource,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.contexts.strategy_runtime_context import (
    StrategyRuntimeContext,
)
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import SignalWriter
from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    IngestionStatusReader,
    StrategyBarReadinessService,
    StrategyEvaluationCheckpointReader,
)
from autonomous_trading_platform.strategy.services.strategy_checkpoint_writer_service import (
    StrategyCheckpointWriter,
)
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)


class SqlAlchemyUniverseMembershipReader:
    def __init__(self, repository: UniverseVersionRepository) -> None:
        self.repository = repository

    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]:
        version = self.repository.get_active_version(as_of)
        if version is None:
            return []
        return self.repository.get_symbols(version.universe_version_id)


# Name recorded on a live builder; reads always go through the daily-version resolver.
LIVE_DATASET_VERSION_PLACEHOLDER = "live_daily_raw_bars"


def build_strategy_runtime_context(
    *,
    session: Session,
    strategy: BaseStrategy,
    dataset_version: str | None = None,
    fallback_dataset: ParquetDataset | None = None,
    fallback_dataset_version: str | None = None,
    use_raw_bars: bool = True,
    lookback_bars: int = 300,
) -> StrategyRuntimeContext:
    """Strategy runtime wired to raw bars with split-adjusted history.

    ``dataset_version`` is the one cumulative raw version a backtest reads. Without it
    (live/paper) the builder resolves the validated daily raw versions covering each
    read window. Strategy history is split-adjusted on read from the stored corporate
    actions (plan 5d, D5/D6); the materialised adjusted dataset is no longer read, so
    ``use_raw_bars`` is kept only for callers that still pass it.
    """
    del use_raw_bars  # raw bars are the only source now
    universe_repository = UniverseVersionRepository(session)
    runtime_state_repository = StrategyRuntimeStateRepository(session)

    universe_reader = SqlAlchemyUniverseMembershipReader(universe_repository)

    bar_reader = HistoricalBarDatasetReader(
        session=session,
        base_path="data",
    )

    lookahead_guard_service = LookaheadGuardService()

    resolver = LiveBarDatasetResolver(session) if dataset_version is None else None
    strategy_context_builder = StrategyContextBuilder(
        market_bar_reader=bar_reader,
        bars_dataset=RAW_BARS_DATASET,
        lookback_bars=lookback_bars,
        lookahead_guard_service=lookahead_guard_service,
        dataset_version=dataset_version or LIVE_DATASET_VERSION_PLACEHOLDER,
        fallback_dataset=fallback_dataset,
        fallback_dataset_version=fallback_dataset_version,
        dataset_version_resolver=resolver.resolve if resolver is not None else None,
        split_source=SorSplitSource(session),
    )

    signal_writer = SignalWriter(session)
    strategy_checkpoint_writer = StrategyCheckpointWriter(
        repository=runtime_state_repository,
        strategy_id=strategy.strategy_id,
    )
    checkpoint_reader = StrategyEvaluationCheckpointReader(
        repository=runtime_state_repository,
        strategy_id=strategy.strategy_id,
    )
    ingestion_status_reader = IngestionStatusReader()

    strategy_evaluation_service = StrategyEvaluationService(
        context_builder=strategy_context_builder,
        universe_reader=universe_reader,
        strategy=strategy,
    )

    strategy_bar_readiness_service = StrategyBarReadinessService(
        ingestion_status_reader=ingestion_status_reader,
        checkpoint_reader=checkpoint_reader,
    )

    run_manifest_service = RunManifestService(session=session)

    return StrategyRuntimeContext(
        strategy_evaluation_service=strategy_evaluation_service,
        strategy_bar_readiness_service=strategy_bar_readiness_service,
        signal_writer=signal_writer,
        strategy_checkpoint_writer=strategy_checkpoint_writer,
        run_manifest_service=run_manifest_service,
    )
