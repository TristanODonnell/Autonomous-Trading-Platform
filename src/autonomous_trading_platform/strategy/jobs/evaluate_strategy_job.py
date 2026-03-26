from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.runtime.services.run_manifest_service import (
    RunManifestService,
)
from autonomous_trading_platform.storage.sor.repositories.signals_repository import SignalRepository
from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    StrategyBarReadinessService,
)
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)


class SignalWriter:
    def __init__(self, session: Session) -> None:
        self.repository = SignalRepository(session)

    def save_many(self, signals: list[Signal]) -> None:
        self.repository.insert_many(signals)


class StrategyCheckpointWriter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def mark_evaluated(self, bar_timestamp: datetime) -> None:
        raise NotImplementedError("Checkpoint persistence not implemented yet.")


@dataclass(frozen=True)
class EvaluateStrategyJobResult:
    evaluated: bool
    reason: str | None
    signals_emitted: int
    target_bar_timestamp: datetime | None
    signals: list[Signal]
    run_id: UUID | None


class EvaluateStrategyJob:
    def __init__(
        self,
        readiness_service: StrategyBarReadinessService,
        evaluation_service: StrategyEvaluationService,
        signal_writer: SignalWriter,
        checkpoint_writer: StrategyCheckpointWriter,
        run_manifest_service: RunManifestService,
    ) -> None:
        self.readiness_service = readiness_service
        self.evaluation_service = evaluation_service
        self.signal_writer = signal_writer
        self.checkpoint_writer = checkpoint_writer
        self.run_manifest_service = run_manifest_service

    def run(self, now: datetime) -> EvaluateStrategyJobResult:
        readiness = self.readiness_service.get_next_ready_bar(now)

        if readiness.target_bar_timestamp is None:
            return EvaluateStrategyJobResult(
                evaluated=False,
                reason=readiness.reason,
                signals_emitted=0,
                target_bar_timestamp=None,
                signals=[],
                run_id=None,
            )

        run_id = uuid4()

        result = self.evaluation_service.evaluate(
            bar_timestamp=readiness.target_bar_timestamp,
            run_id=run_id,
            evaluation_timestamp=now,
        )

        if result.signals:
            self.signal_writer.save_many(result.signals)

        self.checkpoint_writer.mark_evaluated(readiness.target_bar_timestamp)

        manifest = self._build_run_manifest(
            run_id=run_id,
            bar_timestamp=readiness.target_bar_timestamp,
            evaluation_timestamp=now,
            strategy_id=result.strategy_id,
            signals_emitted=len(result.signals),
        )
        self.run_manifest_service.save(manifest)

        return EvaluateStrategyJobResult(
            evaluated=True,
            reason=None,
            signals_emitted=len(result.signals),
            target_bar_timestamp=readiness.target_bar_timestamp,
            signals=result.signals,
            run_id=run_id,
        )

    def _build_run_manifest(
        self,
        *,
        run_id: UUID,
        bar_timestamp: datetime,
        evaluation_timestamp: datetime,
        strategy_id: str,
        signals_emitted: int,
    ) -> RunManifest:
        return RunManifest(
            run_id=run_id,
            run_type=RunType.PAPER,
            environment="dev",
            broker="alpaca",
            broker_account_id="strategy-eval",
            strategy_id=strategy_id,
            strategy_version="v1",
            strategy_config={"signals_emitted": signals_emitted},
            capital_bucket="0",
            interval=BarInterval.FIVE_MIN,
            start_date=bar_timestamp.date(),
            end_date=bar_timestamp.date(),
            dataset_version="unknown",
            universe_version="unknown",
            random_seed=None,
            git_commit="dev",
            docker_image=None,
            python_version=None,
            dependency_lock_hash=None,
            created_at=evaluation_timestamp,
            notes=(
                "Strategy evaluation job run "
                f"for bar {bar_timestamp.isoformat()} "
                f"at {evaluation_timestamp.isoformat()}"
            ),
        )
