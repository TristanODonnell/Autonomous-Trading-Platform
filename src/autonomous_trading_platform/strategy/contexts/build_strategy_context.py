from datetime import datetime, timedelta
from typing import cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.sor.repositories.market_bar_repository import (
    MarketBarRepository,
)
from autonomous_trading_platform.storage.sor.repositories.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.strategy.contexts.strategy_context import StrategyContext
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


class SqlAlchemyMarketBarReader:
    def __init__(self, repository: MarketBarRepository) -> None:
        self.repository = repository

    def get_bars_up_to_timestamp(
        self,
        symbol: str,
        end_timestamp: datetime,
        lookback_bars: int,
    ) -> list[MarketBar]:
        start_timestamp = end_timestamp - timedelta(minutes=5 * max(lookback_bars, 1))

        rows = self.repository.get_bars_for_symbols_between(
            symbols=[symbol],
            start_ts=start_timestamp,
            end_ts=end_timestamp,
        )

        sorted_rows = sorted(rows, key=lambda bar: bar.timestamp)
        trimmed_rows = sorted_rows[-lookback_bars:]

        return cast(list[MarketBar], trimmed_rows)


class SqlAlchemyUniverseMembershipReader:
    def __init__(self, repository: UniverseSnapshotRepository) -> None:
        self.repository = repository

    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]:
        snapshot = self.repository.get_by_snapshot_date(as_of.date())
        if snapshot is None:
            return []
        return list(snapshot.symbols)


def build_strategy_context(
    *,
    session: Session,
    strategy: BaseStrategy,
) -> StrategyContext:
    market_bar_repository = MarketBarRepository(session)
    universe_repository = UniverseSnapshotRepository(session)
    runtime_state_repository = StrategyRuntimeStateRepository(session)

    market_bar_reader = SqlAlchemyMarketBarReader(market_bar_repository)
    universe_reader = SqlAlchemyUniverseMembershipReader(universe_repository)

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
        market_bar_reader=market_bar_reader,
        universe_reader=universe_reader,
        strategy=strategy,
    )

    strategy_bar_readiness_service = StrategyBarReadinessService(
        ingestion_status_reader=ingestion_status_reader,
        checkpoint_reader=checkpoint_reader,
    )

    run_manifest_service = RunManifestService(session=session)

    return StrategyContext(
        strategy_evaluation_service=strategy_evaluation_service,
        strategy_bar_readiness_service=strategy_bar_readiness_service,
        signal_writer=signal_writer,
        strategy_checkpoint_writer=strategy_checkpoint_writer,
        run_manifest_service=run_manifest_service,
    )
