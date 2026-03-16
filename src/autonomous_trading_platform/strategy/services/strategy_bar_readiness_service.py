from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class StrategyBarReadinessResult:
    target_bar_timestamp: datetime | None
    reason: str | None = None


class IngestionStatusReaderProtocol:
    def has_successful_bar_ingestion(self, bar_timestamp: datetime) -> bool:
        raise NotImplementedError


class StrategyEvaluationCheckpointReaderProtocol:
    def get_last_evaluated_bar_timestamp(self) -> datetime | None:
        raise NotImplementedError


class StrategyBarReadinessService:
    """
    Determines whether a completed 5-minute bar is ready for strategy evaluation.

    This service is responsible for:
    - enforcing 5-minute alignment
    - ensuring the bar is complete
    - ensuring ingestion succeeded
    - ensuring monotonic evaluation (no re-evaluating old bars)
    """

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
        """
        Return the next completed and ready bar timestamp for evaluation.

        Args:
            now: Current timestamp used to determine the latest completed bar.

        Returns:
            StrategyBarReadinessResult containing the target bar timestamp if ready.
        """
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
        """
        Compute the latest completed 5-minute bar timestamp.

        Example:
            If now = 10:07, the latest completed 5-minute bar is 10:05.
            If now = 10:05 exactly, 10:05 is considered complete.
        """
        minute_bucket = (now.minute // self.bar_interval_minutes) * self.bar_interval_minutes
        aligned = now.replace(minute=minute_bucket, second=0, microsecond=0)

        if aligned > now:
            aligned -= timedelta(minutes=self.bar_interval_minutes)

        return aligned
