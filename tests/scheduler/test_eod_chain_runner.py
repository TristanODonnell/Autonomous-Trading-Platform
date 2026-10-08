from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper
from autonomous_trading_platform.runtime.services.orphan_job_recovery_service import (
    OrphanJobRecoveryService,
)
from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import (
    ChainContext,
    ChainStatus,
    ChainStep,
    EodChainRunner,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_run_steps import (
    RuntimeJobRunSteps,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns

CHAIN = "test_eod_chain"
DAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)


class _NoSleep(InterruptibleSleeper):
    def __init__(self) -> None:
        super().__init__()
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _runner(session: Session, sleeper: InterruptibleSleeper | None = None) -> EodChainRunner:
    return EodChainRunner(session, sleeper=sleeper or _NoSleep())


def _ok(name: str, calls: list[str], **values: object) -> ChainStep:
    def run(ctx: ChainContext) -> dict[str, object]:
        calls.append(name)
        ctx.values.update(values)
        return {"ran": name}

    return ChainStep(name=name, run=run)


def _child_rows(session: Session, parent_id: str) -> list[RuntimeJobRuns]:
    return list(
        session.scalars(
            select(RuntimeJobRuns)
            .where(RuntimeJobRuns.parent_job_run_id == parent_id)
            .order_by(RuntimeJobRuns.started_at)
        ).all()
    )


def _step_rows(session: Session, parent_id: str) -> dict[str, str]:
    rows = session.scalars(
        select(RuntimeJobRunSteps).where(RuntimeJobRunSteps.job_run_id == parent_id)
    ).all()
    return {row.step_name: row.status for row in rows}


def test_steps_run_in_order_and_pass_values(db_session: Session) -> None:
    calls: list[str] = []
    seen: dict[str, object] = {}

    def reader(ctx: ChainContext) -> None:
        seen.update(ctx.values)
        calls.append("reader")

    result = _runner(db_session).run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[_ok("ingest", calls, dataset_version_id="dv-1"), ChainStep("reader", reader)],
    )

    assert result.status is ChainStatus.COMPLETED
    assert calls == ["ingest", "reader"]
    assert seen == {"dataset_version_id": "dv-1"}
    assert result.parent_job_run_id is not None
    parent = db_session.get(RuntimeJobRuns, result.parent_job_run_id)
    assert parent is not None and parent.status == "completed"
    assert parent.output_summary_json["values"] == {"dataset_version_id": "dv-1"}
    assert [c.job_name for c in _child_rows(db_session, parent.job_run_id)] == [
        f"{CHAIN}.ingest",
        f"{CHAIN}.reader",
    ]
    assert _step_rows(db_session, parent.job_run_id) == {
        "ingest": "completed",
        "reader": "completed",
    }


def test_retries_up_to_the_cap_then_records_failure(db_session: Session) -> None:
    attempts: list[int] = []

    def flaky(ctx: ChainContext) -> None:
        attempts.append(len(attempts) + 1)
        raise RuntimeError(f"boom {len(attempts)}")

    sleeper = _NoSleep()
    result = _runner(db_session, sleeper).run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[ChainStep("flaky", flaky, max_attempts=3, retry_delay_seconds=7)],
    )

    assert attempts == [1, 2, 3]
    assert sleeper.slept == [7, 7]  # no delay after the last attempt
    assert result.status is ChainStatus.COMPLETED_WITH_ERRORS
    assert result.failed_steps == ("flaky",)
    children = _child_rows(db_session, result.parent_job_run_id or "")
    assert [c.status for c in children] == ["failed", "failed", "failed"]
    assert [c.input_summary_json["attempt"] for c in children] == [1, 2, 3]
    assert children[-1].error_message == "boom 3"
    assert _step_rows(db_session, result.parent_job_run_id or "") == {"flaky": "failed"}


def test_second_attempt_can_succeed(db_session: Session) -> None:
    attempts: list[int] = []

    def flaky_then_ok(ctx: ChainContext) -> dict[str, int]:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first try")
        return {"attempt": len(attempts)}

    result = _runner(db_session).run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[ChainStep("flaky", flaky_then_ok, max_attempts=2)],
    )

    assert result.status is ChainStatus.COMPLETED
    children = _child_rows(db_session, result.parent_job_run_id or "")
    assert [c.status for c in children] == ["failed", "completed"]
    assert children[-1].output_summary_json == {"attempt": 2}


def test_blocking_failure_skips_later_steps_and_fails_the_chain(db_session: Session) -> None:
    calls: list[str] = []

    def broken(ctx: ChainContext) -> None:
        raise RuntimeError("no data")

    result = _runner(db_session).run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[
            _ok("first", calls),
            ChainStep("corp_actions", broken, blocking=True, max_attempts=1),
            _ok("features", calls),
            _ok("risk", calls),
        ],
    )

    assert result.status is ChainStatus.FAILED
    assert calls == ["first"]
    assert result.failed_steps == ("corp_actions",)
    assert result.skipped_steps == ("features", "risk")
    steps = _step_rows(db_session, result.parent_job_run_id or "")
    assert steps == {
        "first": "completed",
        "corp_actions": "failed",
        "features": "skipped",
        "risk": "skipped",
    }
    # A failed day is done: the loop must not try again until tomorrow.
    assert _runner(db_session).is_done_for(chain_name=CHAIN, trading_date=DAY) is True
    again = _runner(db_session).run(
        chain_name=CHAIN, trading_date=DAY, now_utc=NOW, steps=[_ok("first", calls)]
    )
    assert again.status is ChainStatus.ALREADY_DONE
    assert calls == ["first"]


def test_independent_failure_lets_the_chain_continue(db_session: Session) -> None:
    calls: list[str] = []

    def broken(ctx: ChainContext) -> None:
        raise RuntimeError("governance hiccup")

    result = _runner(db_session).run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[
            ChainStep("governance", broken, blocking=False, max_attempts=1),
            _ok("ops_health", calls),
        ],
    )

    assert result.status is ChainStatus.COMPLETED_WITH_ERRORS
    assert calls == ["ops_health"]
    assert result.failed_steps == ("governance",)
    assert result.completed_steps == ("ops_health",)


def test_shutdown_between_steps_interrupts_and_resume_skips_completed(db_session: Session) -> None:
    calls: list[str] = []
    sleeper = _NoSleep()

    def first_then_sigterm(ctx: ChainContext) -> None:
        calls.append("first")
        sleeper.request_shutdown()  # SIGTERM arrives while this step is running

    steps = [
        ChainStep("first", first_then_sigterm),
        _ok("second", calls),
        _ok("third", calls),
    ]
    interrupted = _runner(db_session, sleeper).run(
        chain_name=CHAIN, trading_date=DAY, now_utc=NOW, steps=steps
    )

    assert interrupted.status is ChainStatus.INTERRUPTED
    assert calls == ["first"]  # the in-flight step finished, no other started
    assert interrupted.completed_steps == ("first",)
    assert _runner(db_session).is_done_for(chain_name=CHAIN, trading_date=DAY) is False

    # Next process start: carry on after the last completed step.
    resumed = _runner(db_session).run(
        chain_name=CHAIN, trading_date=DAY, now_utc=NOW + timedelta(minutes=10), steps=steps
    )

    assert resumed.status is ChainStatus.COMPLETED
    assert calls == ["first", "second", "third"]
    assert resumed.resumed_steps == ("first",)
    assert resumed.completed_steps == ("first", "second", "third")
    assert _runner(db_session).is_done_for(chain_name=CHAIN, trading_date=DAY) is True


def test_shutdown_during_retry_delay_interrupts(db_session: Session) -> None:
    sleeper = _NoSleep()

    def failing(ctx: ChainContext) -> None:
        sleeper.request_shutdown()
        raise RuntimeError("transient")

    result = _runner(db_session, sleeper).run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[ChainStep("failing", failing, max_attempts=3)],
    )

    assert result.status is ChainStatus.INTERRUPTED
    assert _step_rows(db_session, result.parent_job_run_id or "") == {"failing": "interrupted"}
    assert _runner(db_session).is_done_for(chain_name=CHAIN, trading_date=DAY) is False


def test_crash_rescued_as_orphan_is_resumed_not_treated_as_done(db_session: Session) -> None:
    calls: list[str] = []
    runner = _runner(db_session)
    steps = [_ok("first", calls), _ok("second", calls)]

    # Simulate a crash after the first step: run it, then put the parent back to "running"
    # as a process killed mid-chain would leave it.
    def crash(ctx: ChainContext) -> None:
        raise RuntimeError("process died here")

    runner.run(
        chain_name=CHAIN,
        trading_date=DAY,
        now_utc=NOW,
        steps=[_ok("first", calls), ChainStep("second", crash, max_attempts=1)],
    )
    parent_id = next(
        row.job_run_id
        for row in db_session.scalars(
            select(RuntimeJobRuns).where(RuntimeJobRuns.job_name == CHAIN)
        ).all()
    )
    parent = db_session.get(RuntimeJobRuns, parent_id)
    assert parent is not None
    parent.status = "running"
    parent.completed_at = None
    db_session.commit()
    rescued = OrphanJobRecoveryService(db_session).rescue_orphan_running_jobs(
        cutoff=datetime.now(UTC) + timedelta(hours=1)
    )
    db_session.commit()
    assert any(r.job_run_id == parent_id for r in rescued)

    assert runner.is_done_for(chain_name=CHAIN, trading_date=DAY) is False
    result = runner.run(chain_name=CHAIN, trading_date=DAY, now_utc=NOW, steps=steps)

    assert result.status is ChainStatus.COMPLETED
    assert result.resumed_steps == ("first",)
    assert calls == ["first", "second"]


def test_applies_false_records_skipped(db_session: Session) -> None:
    calls: list[str] = []
    weekly = ChainStep(
        "weekly_review", lambda ctx: calls.append("weekly"), applies=lambda ctx: False
    )

    result = _runner(db_session).run(
        chain_name=CHAIN, trading_date=DAY, now_utc=NOW, steps=[weekly, _ok("daily", calls)]
    )

    assert result.status is ChainStatus.COMPLETED
    assert calls == ["daily"]
    assert result.skipped_steps == ("weekly_review",)
    assert _step_rows(db_session, result.parent_job_run_id or "")["weekly_review"] == "skipped"


def test_chains_for_different_dates_are_independent(db_session: Session) -> None:
    calls: list[str] = []
    runner = _runner(db_session)
    runner.run(chain_name=CHAIN, trading_date=DAY, now_utc=NOW, steps=[_ok("a", calls)])

    other_day = DAY + timedelta(days=1)
    assert runner.is_done_for(chain_name=CHAIN, trading_date=other_day) is False
    result = runner.run(
        chain_name=CHAIN,
        trading_date=other_day,
        now_utc=NOW + timedelta(days=1),
        steps=[_ok("a", calls)],
    )
    assert result.status is ChainStatus.COMPLETED
    assert calls == ["a", "a"]


def test_weekly_review_step_is_a_no_op_while_the_review_mode_is_off(db_session: Session) -> None:
    from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
        PaperTradingGoldenPathOrchestrator,
    )

    orchestrator = PaperTradingGoldenPathOrchestrator(db_session)
    ctx = ChainContext(
        chain_name=CHAIN, trading_date=DAY, now_utc=NOW, session=db_session, parent_job_run_id="p"
    )

    assert orchestrator._step_weekly_portfolio_review(ctx) == {"mode": "off", "review_id": None}
