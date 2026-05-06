from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.common.errors import (
    TransientInfrastructureError,
)
from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
    RunType,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    evaluate_strategy_job_duration,
    evaluate_strategy_job_failures,
    evaluate_strategy_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
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

logger = get_logger(__name__)

EVALUATE_STRATEGY_JOB_METRICS = JobMetricSet(
    runs=evaluate_strategy_job_runs,
    failures=evaluate_strategy_job_failures,
    duration=evaluate_strategy_job_duration,
)


class SignalWriter:
    def __init__(self, session: Session) -> None:
        self.repository = SignalRepository(session)

    def save_many(self, signals: list[Signal]) -> None:
        self.repository.insert_many(signals)


class StrategyCheckpointWriter(Protocol):
    def mark_evaluated(self, bar_timestamp: datetime) -> None: ...


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

    def run(
        self,
        *,
        now: datetime,
        parent_run_id: str,
    ) -> EvaluateStrategyJobResult:
        component = "strategy.jobs.evaluate_strategy_job"
        job = "evaluate_strategy_job"
        job_start = perf_counter()

        record_job_started(
            logger=logger,
            metrics=EVALUATE_STRATEGY_JOB_METRICS,
            job=job,
            component=component,
            run_id=parent_run_id,
        )

        try:
            with start_span(
                "evaluate_strategy_job.run",
                timespan=SpanTimespan.JOB,
            ) as job_span:
                job_span.set_attribute("ratp.run_id", parent_run_id)
                job_span.set_attribute("ratp.component", component)
                job_span.set_attribute("ratp.job", job)
                job_span.set_attribute("ratp.now_utc", now.isoformat())

                readiness = self.readiness_service.get_next_ready_bar(now)

                if readiness.target_bar_timestamp is None:
                    result = EvaluateStrategyJobResult(
                        evaluated=False,
                        reason=readiness.reason,
                        signals_emitted=0,
                        target_bar_timestamp=None,
                        signals=[],
                        run_id=None,
                    )

                    job_span.set_attribute("ratp.evaluated", result.evaluated)
                    if result.reason is not None:
                        job_span.set_attribute("ratp.reason", result.reason)

                    duration = perf_counter() - job_start
                    record_job_completed(
                        logger=logger,
                        metrics=EVALUATE_STRATEGY_JOB_METRICS,
                        job=job,
                        component=component,
                        run_id=parent_run_id,
                        duration_seconds=duration,
                    )
                    return result

                job_span.set_attribute(
                    "ratp.target_bar_timestamp",
                    readiness.target_bar_timestamp.isoformat(),
                )

                evaluation_run_id = uuid4()

                result = self.evaluation_service.evaluate(
                    bar_timestamp=readiness.target_bar_timestamp,
                    run_id=evaluation_run_id,
                    evaluation_timestamp=now,
                )

                if result.signals:
                    self.signal_writer.save_many(result.signals)

                self.checkpoint_writer.mark_evaluated(readiness.target_bar_timestamp)

                manifest = self._build_run_manifest(
                    run_id=evaluation_run_id,
                    bar_timestamp=readiness.target_bar_timestamp,
                    evaluation_timestamp=now,
                    strategy_id=result.strategy_id,
                    signals_emitted=len(result.signals),
                )
                self.run_manifest_service.save(manifest)

                final_result = EvaluateStrategyJobResult(
                    evaluated=True,
                    reason=None,
                    signals_emitted=len(result.signals),
                    target_bar_timestamp=readiness.target_bar_timestamp,
                    signals=result.signals,
                    run_id=evaluation_run_id,
                )

                job_span.set_attribute("ratp.evaluated", final_result.evaluated)
                job_span.set_attribute("ratp.signals_emitted", final_result.signals_emitted)
                job_span.set_attribute("ratp.evaluation_run_id", str(evaluation_run_id))

            duration = perf_counter() - job_start
            record_job_completed(
                logger=logger,
                metrics=EVALUATE_STRATEGY_JOB_METRICS,
                job=job,
                component=component,
                run_id=parent_run_id,
                duration_seconds=duration,
            )
            return final_result

        except Exception as exc:
            duration = perf_counter() - job_start
            record_job_failed(
                logger=logger,
                metrics=EVALUATE_STRATEGY_JOB_METRICS,
                job=job,
                component=component,
                run_id=parent_run_id,
                exc=exc,
                duration_seconds=duration,
                failure_class=(
                    "transient" if isinstance(exc, TransientInfrastructureError) else "unknown"
                ),
            )
            raise

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
            capital_bucket=Decimal("0"),
            interval=BarInterval.FIVE_MIN,
            start_date=bar_timestamp.date(),
            end_date=bar_timestamp.date(),
            dataset_version="unknown",
            price_basis=PriceBasis.RAW,
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
            governance_state=GovernanceState.APPROVED_PAPER,
        )
