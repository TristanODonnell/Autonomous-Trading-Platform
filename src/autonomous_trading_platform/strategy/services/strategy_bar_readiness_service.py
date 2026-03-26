from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, cast

from autonomous_trading_platform.storage.sor.repositories.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)

from ..contracts.strategy_bar_readiness_result import StrategyBarReadinessResult


class IngestionStatusReaderProtocol(Protocol):
    def has_successful_bar_ingestion(self, bar_timestamp: datetime) -> bool: ...


class StrategyEvaluationCheckpointReaderProtocol(Protocol):
    def get_last_evaluated_bar_timestamp(self) -> datetime | None: ...


class IngestionStatusReader:
    def has_successful_bar_ingestion(self, bar_timestamp: datetime) -> bool:
        return True


class StrategyEvaluationCheckpointReader:
    def __init__(
        self,
        repository: StrategyRuntimeStateRepository,
        strategy_id: str,
    ) -> None:
        self.repository = repository
        self.strategy_id = strategy_id

    def get_last_evaluated_bar_timestamp(self) -> datetime | None:
        row = self.repository.get_by_strategy_id(self.strategy_id)
        if row is None:
            return None
        return cast(datetime | None, row.last_evaluated_bar_timestamp)


class StrategyBarReadinessService:
    def __init__(
        self,
        ingestion_status_reader: IngestionStatusReaderProtocol,
        checkpoint_reader: StrategyEvaluationCheckpointReaderProtocol,
        bar_interval_minutes: int = 5,
    ) -> None:
        self.ingestion_status_reader = ingestion_status_reader
        self.checkpoint_reader = checkpoint_reader
        self.bar_interval_minutes = bar_interval_minutes

    def get_next_ready_bar(self, now: datetime) -> StrategyBarReadinessResult:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        target_bar_timestamp = self._latest_completed_bar_timestamp(now)
        last_evaluated = self.checkpoint_reader.get_last_evaluated_bar_timestamp()

        if last_evaluated is not None and target_bar_timestamp <= last_evaluated:
            return StrategyBarReadinessResult(
                target_bar_timestamp=None,
                reason="No newer completed bar is available for evaluation.",
            )

        if not self.ingestion_status_reader.has_successful_bar_ingestion(target_bar_timestamp):
            return StrategyBarReadinessResult(
                target_bar_timestamp=None,
                reason="Bar ingestion has not succeeded for the target timestamp.",
            )

        return StrategyBarReadinessResult(
            target_bar_timestamp=target_bar_timestamp,
            reason=None,
        )

    def _latest_completed_bar_timestamp(self, now: datetime) -> datetime:
        minute_bucket = (now.minute // self.bar_interval_minutes) * self.bar_interval_minutes
        aligned = now.replace(minute=minute_bucket, second=0, microsecond=0)

        if aligned > now:
            aligned -= timedelta(minutes=self.bar_interval_minutes)

        return aligned
