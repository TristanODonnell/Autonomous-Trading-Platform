# autonomous_trading_platform/strategy/contexts/build_strategy_runtime_context.py

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.parquet.datasets import ADJUSTED_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.sor.repositories.core.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_snapshot_repository import (
    UniverseSnapshotRepository,
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
    def __init__(self, repository: UniverseSnapshotRepository) -> None:
        self.repository = repository

    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]:
        snapshot = self.repository.get_by_snapshot_date(as_of.date())
        if snapshot is None:
            return []
        return list(snapshot.symbols)


def build_strategy_runtime_context(
    *,
    session: Session,
    strategy: BaseStrategy,
) -> StrategyRuntimeContext:
    universe_repository = UniverseSnapshotRepository(session)
    runtime_state_repository = StrategyRuntimeStateRepository(session)

    universe_reader = SqlAlchemyUniverseMembershipReader(universe_repository)

    bar_reader = HistoricalBarDatasetReader(
        session=session,
        base_path="data",
    )

    lookahead_guard_service = LookaheadGuardService()

    strategy_context_builder = StrategyContextBuilder(
        market_bar_reader=bar_reader,
        bars_dataset=ADJUSTED_BARS_DATASET,
        lookback_bars=300,
        lookahead_guard_service=lookahead_guard_service,
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
