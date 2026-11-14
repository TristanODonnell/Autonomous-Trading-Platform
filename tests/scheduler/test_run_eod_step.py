from __future__ import annotations

import argparse
import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autonomous_trading_platform import db
from autonomous_trading_platform.cli.commands import runtime_eod_step
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.runtime.clock import FakeMarketCalendar
from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper
from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import (
    ChainContext,
    ChainStep,
    EodChainRunner,
)
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    EOD_CHAIN_JOB_NAME,
    EOD_STEP_JOB_NAME,
    EodStepRefused,
    EodStepResult,
    PaperTradingGoldenPathOrchestrator,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

FRIDAY = date(2026, 11, 13)
MONDAY = date(2026, 11, 16)
# 18:00 ET on a November day is 23:00 UTC.
FRIDAY_CHAIN_CLOCK = datetime(2026, 11, 13, 23, 0, tzinfo=UTC)


class _Orchestrator(PaperTradingGoldenPathOrchestrator):
    """The real single-step runner over a chain of fake steps."""

    def __init__(self, session: Session, steps: list[ChainStep]) -> None:
        super().__init__(session, calendar=FakeMarketCalendar())
        self._steps = steps
        self.resolve_calls = 0

    def eod_chain_steps(self) -> list[ChainStep]:
        return self._steps

    def _step_resolve_raw_bars_dataset(self, ctx: ChainContext) -> dict[str, Any]:
        self.resolve_calls += 1
        ctx.values["dataset_version_id"] = "raw-bars-1"
        return {"dataset_version_id": "raw-bars-1"}


def _step(name: str, seen: list[ChainContext] | None = None, **kwargs: Any) -> ChainStep:
    def run(ctx: ChainContext) -> dict[str, Any]:
        if seen is not None:
            seen.append(ctx)
        return {"ran": name, "dataset": ctx.values.get("dataset_version_id")}

    return ChainStep(name=name, run=run, **kwargs)


def _chain(seen: list[ChainContext] | None = None, **target_kwargs: Any) -> list[ChainStep]:
    return [
        _step("calendar_cross_check", seen, rollback_safe=True),
        _step("resolve_raw_bars_dataset", seen, rollback_safe=True),
        _step("target", seen, **target_kwargs),
    ]


def _job_rows(session: Session, job_name: str) -> list[RuntimeJobRuns]:
    return list(
        session.scalars(select(RuntimeJobRuns).where(RuntimeJobRuns.job_name == job_name)).all()
    )


# -- the single-step runner ---------------------------------------------------------------


def test_runs_the_step_with_the_chain_clock_and_records_a_separate_job(
    db_session: Session,
) -> None:
    seen: list[ChainContext] = []
    orchestrator = _Orchestrator(db_session, _chain(seen))

    result = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False, actor="op")

    assert result.status == "completed"
    assert result.output == {"ran": "target", "dataset": "raw-bars-1"}
    assert result.now_utc == FRIDAY_CHAIN_CLOCK
    assert [ctx.trading_date for ctx in seen] == [FRIDAY]
    assert seen[0].now_utc == FRIDAY_CHAIN_CLOCK

    (row,) = _job_rows(db_session, EOD_STEP_JOB_NAME)
    assert row.job_run_id == result.job_run_id
    assert row.status == "completed"
    assert row.trigger_type == "manual_cli"
    assert row.input_summary_json["step"] == "target"
    assert row.input_summary_json["trading_date"] == "2026-11-13"
    assert row.input_summary_json["actor"] == "op"
    assert row.output_summary_json == {"ran": "target", "dataset": "raw-bars-1"}


def test_a_manual_run_is_invisible_to_the_chains_resume_logic(db_session: Session) -> None:
    _Orchestrator(db_session, _chain()).run_eod_step("target", trading_date=FRIDAY, dry_run=False)

    runner = EodChainRunner(db_session)
    assert _job_rows(db_session, EOD_CHAIN_JOB_NAME) == []
    assert not runner.is_done_for(chain_name=EOD_CHAIN_JOB_NAME, trading_date=FRIDAY)
    assert runner.completed_steps_for(chain_name=EOD_CHAIN_JOB_NAME, trading_date=FRIDAY) == set()


def test_only_steps_after_the_resolve_step_get_its_values(db_session: Session) -> None:
    orchestrator = _Orchestrator(db_session, _chain())

    before = orchestrator.run_eod_step("calendar_cross_check", trading_date=FRIDAY, dry_run=False)
    assert orchestrator.resolve_calls == 0
    assert before.output == {"ran": "calendar_cross_check", "dataset": None}

    after = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False)
    assert orchestrator.resolve_calls == 1
    assert after.output == {"ran": "target", "dataset": "raw-bars-1"}


def test_the_calendar_gate_is_reported_not_enforced(db_session: Session) -> None:
    orchestrator = _Orchestrator(db_session, _chain(applies=lambda ctx: False))

    result = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False)

    assert result.status == "completed"
    assert result.scheduled_for_date is False


def test_a_step_with_no_gate_is_scheduled_on_trading_days_only(db_session: Session) -> None:
    orchestrator = _Orchestrator(db_session, _chain())

    assert orchestrator.run_eod_step(
        "calendar_cross_check", trading_date=FRIDAY, dry_run=False
    ).scheduled_for_date
    saturday = date(2026, 11, 14)
    assert not orchestrator.run_eod_step(
        "calendar_cross_check", trading_date=saturday, dry_run=False
    ).scheduled_for_date


def test_unknown_step_is_refused_with_the_list(db_session: Session) -> None:
    with pytest.raises(EodStepRefused, match="calendar_cross_check, resolve_raw_bars_dataset"):
        _Orchestrator(db_session, _chain()).run_eod_step(
            "rolling_raw_bar", trading_date=FRIDAY, dry_run=True
        )


def test_dry_run_is_refused_for_a_step_that_is_not_rollback_safe(db_session: Session) -> None:
    seen: list[ChainContext] = []
    orchestrator = _Orchestrator(db_session, _chain(seen))

    with pytest.raises(EodStepRefused, match="has no dry run"):
        orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=True)

    assert seen == []
    assert _job_rows(db_session, EOD_STEP_JOB_NAME) == []


def test_dry_run_is_accepted_for_a_rollback_safe_step(db_session: Session) -> None:
    orchestrator = _Orchestrator(db_session, _chain(rollback_safe=True))

    result = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=True)

    assert result.status == "completed"
    assert result.dry_run is True


def test_a_failing_step_is_recorded_and_reported_not_raised(db_session: Session) -> None:
    def boom(ctx: ChainContext) -> dict[str, Any]:
        raise RuntimeError("no rolling sources")

    orchestrator = _Orchestrator(db_session, [*_chain()[:2], ChainStep(name="target", run=boom)])

    result = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False)

    assert result.status == "failed"
    assert result.error == "no rolling sources"
    (row,) = _job_rows(db_session, EOD_STEP_JOB_NAME)
    assert row.status == "failed"
    assert row.error_message == "no rolling sources"


# -- what a real run is refused for -------------------------------------------------------


def _rotation_chain(*, scheduled: bool) -> list[ChainStep]:
    return [
        _step("resolve_raw_bars_dataset", rollback_safe=True),
        _step("monthly_universe_rotation", applies=lambda ctx: scheduled, rollback_safe=True),
    ]


def test_monthly_rotation_off_its_day_is_not_run_for_real(db_session: Session) -> None:
    orchestrator = _Orchestrator(db_session, _rotation_chain(scheduled=False))

    with pytest.raises(EodStepRefused, match="off its scheduled day it can only be rehearsed"):
        orchestrator.run_eod_step("monthly_universe_rotation", trading_date=FRIDAY, dry_run=False)

    assert _job_rows(db_session, EOD_STEP_JOB_NAME) == []


def test_monthly_rotation_on_its_day_runs_for_real(db_session: Session) -> None:
    orchestrator = _Orchestrator(db_session, _rotation_chain(scheduled=True))

    result = orchestrator.run_eod_step(
        "monthly_universe_rotation", trading_date=FRIDAY, dry_run=False
    )

    assert result.status == "completed"
    assert result.scheduled_for_date is True


def _review_chain() -> list[ChainStep]:
    return [
        _step("resolve_raw_bars_dataset", rollback_safe=True),
        _step("weekly_portfolio_review", applies=lambda ctx: False, rollback_safe=True),
    ]


def _set_review_mode(session: Session, mode: str) -> None:
    OperatorSettingsRepository(session).get_or_create_default().portfolio_review_mode = mode
    session.flush()


def test_weekly_review_off_its_day_in_auto_mode_needs_dry_run(db_session: Session) -> None:
    _set_review_mode(db_session, "auto")
    orchestrator = _Orchestrator(db_session, _review_chain())

    with pytest.raises(EodStepRefused, match="portfolio_review_mode is auto"):
        orchestrator.run_eod_step("weekly_portfolio_review", trading_date=FRIDAY, dry_run=False)

    assert (
        orchestrator.run_eod_step(
            "weekly_portfolio_review", trading_date=FRIDAY, dry_run=True
        ).status
        == "completed"
    )


def test_weekly_review_off_its_day_in_advisory_mode_runs_for_real(db_session: Session) -> None:
    _set_review_mode(db_session, "advisory")
    orchestrator = _Orchestrator(db_session, _review_chain())

    result = orchestrator.run_eod_step(
        "weekly_portfolio_review", trading_date=FRIDAY, dry_run=False
    )

    assert result.status == "completed"
    assert result.scheduled_for_date is False


class _NoSleep(InterruptibleSleeper):
    def sleep(self, seconds: float) -> None:
        return None


def test_a_step_the_chain_already_completed_needs_force(db_session: Session) -> None:
    steps = _chain(rollback_safe=True)
    EodChainRunner(db_session, sleeper=_NoSleep()).run(
        chain_name=EOD_CHAIN_JOB_NAME,
        trading_date=FRIDAY,
        now_utc=FRIDAY_CHAIN_CLOCK,
        steps=steps,
    )
    orchestrator = _Orchestrator(db_session, steps)

    with pytest.raises(EodStepRefused, match="already completed target for 2026-11-13"):
        orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False)

    # A rehearsal of it is fine, so is another date, and --force overrides.
    assert orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=True).status == (
        "completed"
    )
    assert orchestrator.run_eod_step("target", trading_date=MONDAY, dry_run=False).status == (
        "completed"
    )
    forced = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False, force=True)
    assert forced.status == "completed"


def test_refused_while_the_scheduled_chain_is_running(db_session: Session) -> None:
    RuntimeJobRunRepository(db_session).save(
        RuntimeJobRun(
            job_run_id="chain-parent-1",
            job_name=EOD_CHAIN_JOB_NAME,
            parent_job_run_id=None,
            status="running",
            trigger_type="scheduler",
            started_at=FRIDAY_CHAIN_CLOCK,
            completed_at=None,
            duration_ms=None,
            error_message=None,
            correlation_id="corr-1",
            input_summary_json={"trading_date": FRIDAY.isoformat()},
            output_summary_json=None,
        )
    )
    orchestrator = _Orchestrator(db_session, _chain(rollback_safe=True))

    for dry_run in (True, False):
        with pytest.raises(EodStepRefused, match="scheduled chain is running"):
            orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=dry_run)

    forced = orchestrator.run_eod_step("target", trading_date=FRIDAY, dry_run=False, force=True)
    assert forced.status == "completed"


# -- the real chain's classification ------------------------------------------------------


def test_real_chain_marks_only_session_bound_steps_rollback_safe(db_session: Session) -> None:
    """Pinned on purpose: flipping a step to rollback_safe is a claim that nothing it
    does escapes the chain's session, and that claim should be made in review."""
    steps = PaperTradingGoldenPathOrchestrator(
        db_session, calendar=FakeMarketCalendar()
    ).eod_chain_steps()

    assert {step.name for step in steps if step.rollback_safe} == {
        "calendar_cross_check",
        "resolve_raw_bars_dataset",
        "broker_reconciliation",
        "sleeve_resync",
        "risk",
        "governance",
        "correlation_monitoring",
        "allocation_rebalance",
        "weekly_portfolio_review",
        "operations_health",
    }
    # Their own session, Parquet, or S3: a rollback of the chain's session misses them.
    assert {step.name for step in steps if not step.rollback_safe} == {
        "final_ingestion",
        "corporate_actions",
        "features",
        "monthly_universe_rotation",
        "rolling_raw_bars",
        "publish_datasets",
    }


def test_real_monthly_rotation_is_never_run_off_schedule_and_names_its_rehearsal(
    db_session: Session,
) -> None:
    """Its candidate scoring commits on a session of its own, so it has no dry run here;
    off its day it is refused both ways and points at the command that rehearses it."""
    orchestrator = PaperTradingGoldenPathOrchestrator(db_session, calendar=FakeMarketCalendar())

    for dry_run in (True, False):
        with pytest.raises(EodStepRefused, match="atp universe monthly-rotation --dry-run"):
            orchestrator.run_eod_step(
                "monthly_universe_rotation", trading_date=FRIDAY, dry_run=dry_run
            )

    assert _job_rows(db_session, EOD_STEP_JOB_NAME) == []


# -- the rolled-back session --------------------------------------------------------------


def test_rollback_only_session_discards_committed_work(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # pysqlite only honours SAVEPOINT inside a transaction it was told to BEGIN.
    @event.listens_for(engine, "connect")
    def _no_implicit_transactions(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _emit_begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")

    with engine.begin() as connection:
        connection.execute(text("create table notes (body text)"))
        connection.execute(text("insert into notes values ('kept')"))
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    with db.rollback_only_session() as session:
        session.execute(text("insert into notes values ('committed inside')"))
        session.commit()
        session.execute(text("insert into notes values ('rolled back inside')"))
        session.rollback()
        session.execute(text("insert into notes values ('committed again')"))
        session.commit()
        inside = session.execute(text("select body from notes order by rowid")).scalars().all()

    assert inside == ["kept", "committed inside", "committed again"]
    with engine.connect() as connection:
        assert connection.execute(text("select body from notes")).scalars().all() == ["kept"]


# -- the command --------------------------------------------------------------------------


def _extract_json(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output[output.index("{") :]))


def test_command_is_registered() -> None:
    args = build_parser().parse_args(
        ["runtime", "run-eod-step", "rolling_raw_bars", "--as-of", "2026-11-13", "--force"]
    )

    assert args.func is runtime_eod_step.handle_run_eod_step
    assert args.step == "rolling_raw_bars"
    assert args.as_of == FRIDAY
    assert args.dry_run is False
    assert args.force is True


def _patch_command(
    monkeypatch: pytest.MonkeyPatch, outcome: EodStepResult | Exception
) -> dict[str, Any]:
    used: dict[str, Any] = {}

    @contextmanager
    def fake_rollback_only_session() -> Generator[str, None, None]:
        used["session"] = "rollback_only"
        yield "rollback-only-session"

    class _Session:
        def close(self) -> None:
            used["closed"] = True

    def fake_get_session() -> _Session:
        used["session"] = "normal"
        return _Session()

    class _FakeOrchestrator:
        def __init__(self, session: Any) -> None:
            pass

        def run_eod_step(self, step_name: str, **kwargs: Any) -> EodStepResult:
            used["call"] = {"step": step_name, **kwargs}
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(runtime_eod_step, "rollback_only_session", fake_rollback_only_session)
    monkeypatch.setattr(runtime_eod_step, "get_session", fake_get_session)
    monkeypatch.setattr(runtime_eod_step, "PaperTradingGoldenPathOrchestrator", _FakeOrchestrator)
    return used


def _args(**overrides: Any) -> argparse.Namespace:
    values = {"step": "risk", "as_of": FRIDAY, "dry_run": False, "force": False, "actor": "op"}
    return argparse.Namespace(**{**values, **overrides})


def _result(**overrides: Any) -> EodStepResult:
    values: dict[str, Any] = {
        "step": "risk",
        "trading_date": FRIDAY,
        "now_utc": FRIDAY_CHAIN_CLOCK,
        "dry_run": False,
        "scheduled_for_date": True,
        "status": "completed",
        "job_run_id": "job-1",
        "output": {"snapshots": 2},
        "error": None,
    }
    return EodStepResult(**{**values, **overrides})


def test_command_dry_run_uses_the_rolled_back_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = _patch_command(monkeypatch, _result(dry_run=True))

    assert runtime_eod_step.handle_run_eod_step(_args(dry_run=True)) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert used["session"] == "rollback_only"
    assert used["call"] == {
        "step": "risk",
        "trading_date": FRIDAY,
        "dry_run": True,
        "force": False,
        "actor": "op",
    }
    assert payload["dry_run"] is True
    assert payload["job_run_id"] is None
    assert payload["output"] == {"snapshots": 2}
    assert payload["now_utc"] == "2026-11-13T23:00:00+00:00"


def test_command_real_run_uses_a_normal_session_and_closes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = _patch_command(monkeypatch, _result())

    assert runtime_eod_step.handle_run_eod_step(_args()) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert used["session"] == "normal"
    assert used["closed"] is True
    assert payload["job_run_id"] == "job-1"
    assert payload["status"] == "completed"


def test_command_exit_codes_for_failed_and_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_command(monkeypatch, _result(status="failed", output=None, error="boom"))
    assert runtime_eod_step.handle_run_eod_step(_args()) == 1
    assert _extract_json(capsys.readouterr().out)["error"] == "boom"

    _patch_command(monkeypatch, EodStepRefused("risk has no dry run"))
    assert runtime_eod_step.handle_run_eod_step(_args(dry_run=True)) == 2
    assert "risk has no dry run" in capsys.readouterr().out
