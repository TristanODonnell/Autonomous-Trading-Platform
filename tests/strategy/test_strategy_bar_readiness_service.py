from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    StrategyBarReadinessService,
)


class StubIngestionStatusReader:
    def __init__(self, successful_timestamps: set[datetime] | None = None) -> None:
        self.successful_timestamps = successful_timestamps or set()
        self.calls: list[datetime] = []

    def has_successful_bar_ingestion(self, bar_timestamp: datetime) -> bool:
        self.calls.append(bar_timestamp)
        return bar_timestamp in self.successful_timestamps


class StubCheckpointReader:
    def __init__(self, last_evaluated: datetime | None) -> None:
        self.last_evaluated = last_evaluated

    def get_last_evaluated_bar_timestamp(self) -> datetime | None:
        return self.last_evaluated


class TestStrategyBarReadinessService:
    def test_returns_false_until_target_bar_ingestion_has_succeeded(self) -> None:
        now = datetime(2025, 1, 1, 15, 37, tzinfo=UTC)

        service = StrategyBarReadinessService(
            ingestion_status_reader=StubIngestionStatusReader(successful_timestamps=set()),
            checkpoint_reader=StubCheckpointReader(last_evaluated=None),
        )

        result = service.get_next_ready_bar(now)

        assert result.target_bar_timestamp is None
        assert result.reason == "Bar ingestion has not succeeded for the target timestamp."

    def test_returns_ready_bar_when_ingestion_has_succeeded(self) -> None:
        target_bar = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)
        now = datetime(2025, 1, 1, 15, 37, tzinfo=UTC)

        service = StrategyBarReadinessService(
            ingestion_status_reader=StubIngestionStatusReader(successful_timestamps={target_bar}),
            checkpoint_reader=StubCheckpointReader(last_evaluated=None),
        )

        result = service.get_next_ready_bar(now)

        assert result.target_bar_timestamp == target_bar
        assert result.reason is None

    def test_ensures_bar_is_evaluated_only_once(self) -> None:
        target_bar = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)
        now = datetime(2025, 1, 1, 15, 37, tzinfo=UTC)

        service = StrategyBarReadinessService(
            ingestion_status_reader=StubIngestionStatusReader(successful_timestamps={target_bar}),
            checkpoint_reader=StubCheckpointReader(last_evaluated=target_bar),
        )

        result = service.get_next_ready_bar(now)

        assert result.target_bar_timestamp is None
        assert result.reason == "No newer completed bar is available for evaluation."

    def test_raises_for_naive_now(self) -> None:
        service = StrategyBarReadinessService(
            ingestion_status_reader=StubIngestionStatusReader(),
            checkpoint_reader=StubCheckpointReader(last_evaluated=None),
        )

        with pytest.raises(ValueError, match="now must be timezone-aware"):
            service.get_next_ready_bar(datetime(2025, 1, 1, 15, 37))
