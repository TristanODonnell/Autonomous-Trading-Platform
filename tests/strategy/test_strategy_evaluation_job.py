from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from autonomous_trading_platform.runtime.services.run_manifest_service import (
    RunManifestService,
)
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import (
    EvaluateStrategyJob,
    SignalWriter,
    StrategyCheckpointWriter,
)
from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    StrategyBarReadinessService,
)
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)


@dataclass(frozen=True)
class FakeReadinessResult:
    target_bar_timestamp: datetime | None
    reason: str | None


@dataclass(frozen=True)
class FakeEvaluationResult:
    strategy_id: str
    bar_timestamp: datetime
    signals: list[Any]


class StubReadinessService:
    def __init__(self, result: FakeReadinessResult) -> None:
        self.result = result
        self.calls: list[datetime] = []

    def get_next_ready_bar(self, now: datetime) -> FakeReadinessResult:
        self.calls.append(now)
        return self.result


class StubEvaluationService:
    def __init__(self, result: FakeEvaluationResult) -> None:
        self.result = result
        self.calls: list[tuple[datetime, UUID, datetime]] = []

    def evaluate(
        self,
        *,
        bar_timestamp: datetime,
        run_id: UUID,
        evaluation_timestamp: datetime,
    ) -> FakeEvaluationResult:
        self.calls.append((bar_timestamp, run_id, evaluation_timestamp))
        return self.result


class StubSignalWriter:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def save_many(self, signals: list[Any]) -> None:
        self.calls.append(signals)


class StubCheckpointWriter:
    def __init__(self) -> None:
        self.calls: list[datetime] = []

    def mark_evaluated(self, bar_timestamp: datetime) -> None:
        self.calls.append(bar_timestamp)


class StubRunManifestService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def save(self, manifest: Any) -> None:
        self.calls.append(manifest)


def test_run_returns_not_evaluated_when_no_bar_is_ready() -> None:
    now = datetime(2025, 1, 1, 15, 36, tzinfo=UTC)

    evaluation_service = StubEvaluationService(
        FakeEvaluationResult(
            strategy_id="test_strategy_v1",
            bar_timestamp=datetime(2025, 1, 1, 15, 35, tzinfo=UTC),
            signals=["signal-aapl"],
        )
    )
    signal_writer = StubSignalWriter()
    checkpoint_writer = StubCheckpointWriter()
    run_manifest_service = StubRunManifestService()

    job = EvaluateStrategyJob(
        readiness_service=cast(
            StrategyBarReadinessService,
            StubReadinessService(
                FakeReadinessResult(
                    target_bar_timestamp=None,
                    reason="No newer completed bar is available for evaluation.",
                )
            ),
        ),
        evaluation_service=cast(StrategyEvaluationService, evaluation_service),
        signal_writer=cast(SignalWriter, signal_writer),
        checkpoint_writer=cast(StrategyCheckpointWriter, checkpoint_writer),
        run_manifest_service=cast(RunManifestService, run_manifest_service),
    )

    result = job.run(
        now=now,
        parent_run_id="test-run-id",
    )

    assert result.evaluated is False
    assert result.reason == "No newer completed bar is available for evaluation."
    assert result.signals_emitted == 0
    assert result.target_bar_timestamp is None
    assert result.signals == []
    assert result.run_id is None

    assert evaluation_service.calls == []
    assert signal_writer.calls == []
    assert checkpoint_writer.calls == []
    assert run_manifest_service.calls == []


def test_run_writes_signals_marks_checkpoint_and_saves_manifest() -> None:
    now = datetime(2025, 1, 1, 15, 36, tzinfo=UTC)
    bar_timestamp = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)

    readiness_service = StubReadinessService(
        FakeReadinessResult(
            target_bar_timestamp=bar_timestamp,
            reason=None,
        )
    )
    evaluation_service = StubEvaluationService(
        FakeEvaluationResult(
            strategy_id="test_strategy_v1",
            bar_timestamp=bar_timestamp,
            signals=["signal-aapl", "signal-msft"],
        )
    )
    signal_writer = StubSignalWriter()
    checkpoint_writer = StubCheckpointWriter()
    run_manifest_service = StubRunManifestService()

    job = EvaluateStrategyJob(
        readiness_service=cast(StrategyBarReadinessService, readiness_service),
        evaluation_service=cast(StrategyEvaluationService, evaluation_service),
        signal_writer=cast(SignalWriter, signal_writer),
        checkpoint_writer=cast(StrategyCheckpointWriter, checkpoint_writer),
        run_manifest_service=cast(RunManifestService, run_manifest_service),
    )

    result = job.run(
        now=now,
        parent_run_id="test-run-id",
    )

    assert result.evaluated is True
    assert result.reason is None
    assert result.signals_emitted == 2
    assert result.target_bar_timestamp == bar_timestamp
    assert result.signals == ["signal-aapl", "signal-msft"]
    assert result.run_id is not None

    assert signal_writer.calls == [["signal-aapl", "signal-msft"]]
    assert checkpoint_writer.calls == [bar_timestamp]

    assert len(run_manifest_service.calls) == 1
    manifest = run_manifest_service.calls[0]
    assert manifest.run_id == result.run_id
    assert manifest.strategy_id == "test_strategy_v1"


def test_run_does_not_write_signals_when_no_signals_are_emitted() -> None:
    now = datetime(2025, 1, 1, 15, 36, tzinfo=UTC)
    bar_timestamp = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)

    readiness_service = StubReadinessService(
        FakeReadinessResult(
            target_bar_timestamp=bar_timestamp,
            reason=None,
        )
    )
    evaluation_service = StubEvaluationService(
        FakeEvaluationResult(
            strategy_id="test_strategy_v1",
            bar_timestamp=bar_timestamp,
            signals=[],
        )
    )
    signal_writer = StubSignalWriter()
    checkpoint_writer = StubCheckpointWriter()
    run_manifest_service = StubRunManifestService()

    job = EvaluateStrategyJob(
        readiness_service=cast(StrategyBarReadinessService, readiness_service),
        evaluation_service=cast(StrategyEvaluationService, evaluation_service),
        signal_writer=cast(SignalWriter, signal_writer),
        checkpoint_writer=cast(StrategyCheckpointWriter, checkpoint_writer),
        run_manifest_service=cast(RunManifestService, run_manifest_service),
    )

    result = job.run(
        now=now,
        parent_run_id="test-run-id",
    )

    assert result.evaluated is True
    assert result.reason is None
    assert result.signals_emitted == 0
    assert result.target_bar_timestamp == bar_timestamp
    assert result.signals == []
    assert result.run_id is not None

    assert signal_writer.calls == []
    assert checkpoint_writer.calls == [bar_timestamp]
    assert len(run_manifest_service.calls) == 1
