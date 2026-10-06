"""Step runner for the end-of-day chain on the paper/live box.

Runs named steps in order, one trading date at a time. Every attempt of every step is a
child ``runtime_job_runs`` row under one parent row per (chain, trading date), and each
step's outcome is also written to ``runtime_job_run_steps`` for the pipeline panel.

Compared with calling the steps in a row this adds what unattended running needs:

* retries with an interruptible delay and a per-step attempt cap, after which the chain
  stops for the day instead of being retried on every loop iteration;
* a shutdown check between steps, so a deploy (SIGTERM) finishes the step in flight and
  starts no other;
* resume: a chain interrupted by a shutdown or a crash continues after the last completed
  step on the next run, and a chain already completed or failed for the date is not re-run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.contracts.runtime.runtime_job_run_step import RuntimeJobRunStep
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper
from autonomous_trading_platform.runtime.services.orphan_job_recovery_service import (
    _RESCUED_ERROR as ORPHAN_RESCUED_ERROR,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_step_repository import (
    RuntimeJobRunStepRepository,
)

logger = get_logger(__name__)

StepOutput = Mapping[str, Any] | None


@dataclass
class ChainContext:
    """What a step sees: the date being closed, the clock, a session, and values earlier
    steps left for later ones (for example the day's ``dataset_version_id``)."""

    chain_name: str
    trading_date: date
    now_utc: datetime
    session: Session
    parent_job_run_id: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChainStep:
    name: str
    run: Callable[[ChainContext], StepOutput]
    # A failed blocking step skips every later step; an independent step's failure is
    # recorded and the chain continues.
    blocking: bool = False
    max_attempts: int = 3
    retry_delay_seconds: float = 30.0
    # Return False to leave the step out for this date; it is recorded as skipped.
    applies: Callable[[ChainContext], bool] | None = None


class ChainStatus(StrEnum):
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    ALREADY_DONE = "already_done"


_DONE_STATUSES = frozenset(
    {ChainStatus.COMPLETED.value, ChainStatus.COMPLETED_WITH_ERRORS.value, ChainStatus.FAILED.value}
)


@dataclass(frozen=True)
class ChainResult:
    status: ChainStatus
    parent_job_run_id: str | None
    correlation_id: str | None
    completed_steps: tuple[str, ...]
    failed_steps: tuple[str, ...]
    skipped_steps: tuple[str, ...]
    resumed_steps: tuple[str, ...]


class EodChainRunner:
    def __init__(
        self,
        session: Session,
        *,
        sleeper: InterruptibleSleeper | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._sleeper = sleeper or InterruptibleSleeper()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._runs = RuntimeJobRunRepository(session)
        self._steps = RuntimeJobRunStepRepository(session)

    # ------------------------------------------------------------------ queries

    def is_done_for(self, *, chain_name: str, trading_date: date) -> bool:
        """True when the chain completed or failed for the date: nothing more to run today."""
        return any(
            row.status in _DONE_STATUSES and not is_resumable_parent(row)
            for row in self._parents(chain_name, trading_date)
        )

    def completed_steps_for(self, *, chain_name: str, trading_date: date) -> set[str]:
        """Step names with a completed attempt under any of the date's parent rows."""
        completed: set[str] = set()
        for parent in self._parents(chain_name, trading_date):
            for child in self._runs.list_children(parent_job_run_id=parent.job_run_id):
                if child.status == "completed":
                    completed.add(_step_name(child.job_name, chain_name))
        return completed

    def _parents(self, chain_name: str, trading_date: date) -> list[RuntimeJobRuns]:
        rows = self._session.scalars(
            select(RuntimeJobRuns)
            .where(RuntimeJobRuns.job_name == chain_name)
            .order_by(RuntimeJobRuns.started_at)
        ).all()
        return [
            row
            for row in rows
            if (row.input_summary_json or {}).get("trading_date") == trading_date.isoformat()
        ]

    # --------------------------------------------------------------------- run

    def run(
        self,
        *,
        chain_name: str,
        trading_date: date,
        now_utc: datetime,
        steps: list[ChainStep],
        trigger_type: str = "scheduler",
    ) -> ChainResult:
        if self.is_done_for(chain_name=chain_name, trading_date=trading_date):
            return ChainResult(ChainStatus.ALREADY_DONE, None, None, (), (), (), ())

        already_completed = self.completed_steps_for(
            chain_name=chain_name, trading_date=trading_date
        )
        resumed = tuple(step.name for step in steps if step.name in already_completed)

        parent_id = str(uuid4())
        correlation_id = str(uuid4())
        started_at = self._clock()
        self._runs.save(
            RuntimeJobRun(
                job_run_id=parent_id,
                job_name=chain_name,
                parent_job_run_id=None,
                status="running",
                trigger_type=trigger_type,
                started_at=started_at,
                completed_at=None,
                duration_ms=None,
                error_message=None,
                correlation_id=correlation_id,
                input_summary_json={
                    "trading_date": trading_date.isoformat(),
                    "now_utc": now_utc.isoformat(),
                    "steps": [step.name for step in steps],
                    "resumed_steps": list(resumed),
                },
                output_summary_json=None,
            )
        )
        ctx = ChainContext(
            chain_name=chain_name,
            trading_date=trading_date,
            now_utc=now_utc,
            session=self._session,
            parent_job_run_id=parent_id,
        )

        completed: list[str] = list(resumed)
        failed: list[str] = []
        skipped: list[str] = []
        status = ChainStatus.COMPLETED
        blocked_by: str | None = None

        for sequence, step in enumerate(steps, start=1):
            if step.name in already_completed:
                continue
            if self._sleeper.is_shutdown:
                status = ChainStatus.INTERRUPTED
                break
            if blocked_by is not None:
                self._record_step(
                    ctx, step, sequence, "skipped", error=f"blocked by failed step {blocked_by}"
                )
                skipped.append(step.name)
                continue
            if step.applies is not None and not step.applies(ctx):
                self._record_step(ctx, step, sequence, "skipped")
                skipped.append(step.name)
                continue

            outcome = self._run_step(ctx, step, sequence, correlation_id, trigger_type)
            if outcome == "completed":
                completed.append(step.name)
            elif outcome == "interrupted":
                status = ChainStatus.INTERRUPTED
                break
            else:
                failed.append(step.name)
                if step.blocking:
                    blocked_by = step.name

        if status is not ChainStatus.INTERRUPTED:
            if blocked_by is not None:
                status = ChainStatus.FAILED
            elif failed:
                status = ChainStatus.COMPLETED_WITH_ERRORS

        completed_at = self._clock()
        self._runs.save(
            RuntimeJobRun(
                job_run_id=parent_id,
                job_name=chain_name,
                parent_job_run_id=None,
                status=status.value,
                trigger_type=trigger_type,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=int((completed_at - started_at).total_seconds() * 1000),
                error_message=(f"blocking step failed: {blocked_by}" if blocked_by else None),
                correlation_id=correlation_id,
                input_summary_json={
                    "trading_date": trading_date.isoformat(),
                    "now_utc": now_utc.isoformat(),
                    "steps": [step.name for step in steps],
                    "resumed_steps": list(resumed),
                },
                output_summary_json={
                    "completed_steps": completed,
                    "failed_steps": failed,
                    "skipped_steps": skipped,
                    "values": {k: _jsonable(v) for k, v in ctx.values.items()},
                },
            )
        )
        logger.info(
            "eod_chain.finished",
            extra={
                "chain": chain_name,
                "trading_date": trading_date.isoformat(),
                "status": status.value,
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
            },
        )
        return ChainResult(
            status,
            parent_id,
            correlation_id,
            tuple(completed),
            tuple(failed),
            tuple(skipped),
            resumed,
        )

    # ------------------------------------------------------------------- steps

    def _run_step(
        self,
        ctx: ChainContext,
        step: ChainStep,
        sequence: int,
        correlation_id: str,
        trigger_type: str,
    ) -> str:
        """Run one step with retries. Returns completed | failed | interrupted."""
        step_started = self._clock()
        last_error: Exception | None = None
        for attempt in range(1, step.max_attempts + 1):
            child_id = str(uuid4())
            attempt_started = self._clock()

            def attempt_row(
                status: str,
                *,
                completed_at: datetime | None = None,
                error_message: str | None = None,
                output: StepOutput = None,
                _child_id: str = child_id,
                _started: datetime = attempt_started,
                _attempt: int = attempt,
            ) -> RuntimeJobRun:
                return RuntimeJobRun(
                    job_run_id=_child_id,
                    job_name=f"{ctx.chain_name}.{step.name}",
                    parent_job_run_id=ctx.parent_job_run_id,
                    status=status,
                    trigger_type=trigger_type,
                    started_at=_started,
                    completed_at=completed_at,
                    duration_ms=(
                        int((completed_at - _started).total_seconds() * 1000)
                        if completed_at is not None
                        else None
                    ),
                    error_message=error_message,
                    correlation_id=correlation_id,
                    input_summary_json={
                        "trading_date": ctx.trading_date.isoformat(),
                        "attempt": _attempt,
                        "max_attempts": step.max_attempts,
                    },
                    output_summary_json=dict(output) if output is not None else None,
                )

            self._runs.save(attempt_row("running"))
            try:
                output = step.run(ctx)
            except Exception as exc:
                self._session.rollback()
                last_error = exc
                self._runs.save(
                    attempt_row("failed", completed_at=self._clock(), error_message=str(exc)[:2000])
                )
                logger.warning(
                    "eod_chain.step_failed",
                    extra={
                        "chain": ctx.chain_name,
                        "step": step.name,
                        "attempt": attempt,
                        "max_attempts": step.max_attempts,
                        "error": str(exc),
                    },
                )
                if attempt < step.max_attempts:
                    self._sleeper.sleep(step.retry_delay_seconds)
                    if self._sleeper.is_shutdown:
                        self._record_step(
                            ctx, step, sequence, "interrupted", started=step_started, error=str(exc)
                        )
                        return "interrupted"
                continue

            self._runs.save(attempt_row("completed", completed_at=self._clock(), output=output))
            self._record_step(ctx, step, sequence, "completed", started=step_started)
            return "completed"

        self._record_step(
            ctx,
            step,
            sequence,
            "failed",
            started=step_started,
            error=str(last_error) if last_error else None,
            error_type=type(last_error).__name__ if last_error else None,
        )
        return "failed"

    def _record_step(
        self,
        ctx: ChainContext,
        step: ChainStep,
        sequence: int,
        status: str,
        *,
        started: datetime | None = None,
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        now = self._clock()
        started = started or now
        self._steps.append(
            RuntimeJobRunStep(
                step_id=uuid4(),
                job_run_id=ctx.parent_job_run_id,
                step_name=step.name,
                status=status,
                sequence_number=sequence,
                started_at=started,
                completed_at=now,
                duration_ms=int((now - started).total_seconds() * 1000),
                error_message=error[:2000] if error else None,
                error_type=error_type,
            )
        )
        self._session.commit()


def is_resumable_parent(row: RuntimeJobRuns) -> bool:
    """A parent row that stopped without finishing: a shutdown, or a crash that the orphan
    rescue later marked failed. Its completed children still count on resume."""
    return bool(
        row.status == ChainStatus.INTERRUPTED.value
        or (row.status == "failed" and row.error_message == ORPHAN_RESCUED_ERROR)
    )


def _step_name(job_name: str, chain_name: str) -> str:
    prefix = f"{chain_name}."
    return job_name[len(prefix) :] if job_name.startswith(prefix) else job_name


def _jsonable(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
