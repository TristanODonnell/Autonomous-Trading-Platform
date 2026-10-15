"""The missing-bars SLA record and the hard stop are two different thresholds.

On the free IEX feed thin names print no trade in some minutes, so a fifth of the
universe missing one 5-minute bar is normal. That is recorded (incidents + SLA breach)
but must not fail the tick; only a mostly-empty window (feed down) does.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.market_data.jobs import ingest_bars_job as module
from autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job import (
    DEFAULT_MISSING_BAR_FAIL_RATIO,
    MISSING_BAR_FAIL_RATIO_ENV,
    MISSING_BAR_SLA_RATIO,
    IngestBarsJob,
    resolve_missing_bar_fail_ratio,
)

CYCLE_TS = datetime(2026, 10, 12, 13, 55, tzinfo=UTC)


class _AuditLogger:
    def __init__(self) -> None:
        self.sla_breaches: list[dict] = []
        self.missing: list[str] = []

    def record_sla_breach(self, *, run_id: str, component: str, message: str, metadata: dict):
        self.sla_breaches.append(metadata)

    def record_bar_missing(self, *, run_id: str, symbol: str, cycle_timestamp: datetime):
        self.missing.append(symbol)


class _QualityRecorder:
    def record_symbol_date_quality(self, *, coverage_rows, incidents) -> None:
        pass


def _job(
    db_session: Session,
    *,
    expected: int,
    received: int,
    enforce_lateness: bool = True,
    fail_ratio: float | None = None,
) -> tuple[IngestBarsJob, _AuditLogger]:
    symbols = {f"S{i:02d}" for i in range(expected)}
    audit = _AuditLogger()
    job = IngestBarsJob(
        expected_symbols=symbols,
        session=db_session,
        run_id="run-1",
        audit_logger=audit,  # type: ignore[arg-type]
        ingestion_run_id="ing-1",
        dataset_version_id="raw_bars_test",
        enforce_lateness=enforce_lateness,
        missing_bar_fail_ratio=fail_ratio,
    )
    job.ingestion_quality_recorder_service = _QualityRecorder()  # type: ignore[assignment]
    job.received_symbols = set(sorted(symbols)[:received])
    return job, audit


def test_defaults_record_sla_breach_but_do_not_fail_the_tick(db_session: Session) -> None:
    # Monday 2026-10-12 on Box A: 5 of 22 symbols (22.7%) had no IEX bar at 09:55 ET.
    job, audit = _job(db_session, expected=22, received=17)

    job.finalize_cycle(CYCLE_TS)  # no raise

    assert len(audit.missing) == 5
    assert len(audit.sla_breaches) == 1
    breach = audit.sla_breaches[0]
    assert breach["threshold"] == MISSING_BAR_SLA_RATIO == 0.2
    assert breach["fail_threshold"] == DEFAULT_MISSING_BAR_FAIL_RATIO == 0.5
    assert breach["missing_count"] == 5


def test_mostly_empty_window_still_fails_the_tick(db_session: Session) -> None:
    job, audit = _job(db_session, expected=20, received=8)  # 60% missing

    with pytest.raises(RuntimeError, match="Too many missing bars \\(60.00%\\)"):
        job.finalize_cycle(CYCLE_TS)
    assert len(audit.sla_breaches) == 1  # the record is written before the stop


def test_fail_ratio_is_configurable_per_job(db_session: Session) -> None:
    job, _ = _job(db_session, expected=20, received=15, fail_ratio=0.2)  # 25% missing

    with pytest.raises(RuntimeError):
        job.finalize_cycle(CYCLE_TS)


def test_replay_never_fails_on_missing_bars(db_session: Session) -> None:
    job, audit = _job(db_session, expected=20, received=2, enforce_lateness=False)

    job.finalize_cycle(CYCLE_TS)  # bars arrive per symbol in replay; nothing to enforce

    assert len(audit.sla_breaches) == 1


def test_fail_ratio_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MISSING_BAR_FAIL_RATIO_ENV, raising=False)
    assert resolve_missing_bar_fail_ratio() == DEFAULT_MISSING_BAR_FAIL_RATIO

    monkeypatch.setenv(MISSING_BAR_FAIL_RATIO_ENV, "0.75")
    assert resolve_missing_bar_fail_ratio() == 0.75

    monkeypatch.setenv(MISSING_BAR_FAIL_RATIO_ENV, " ")
    assert resolve_missing_bar_fail_ratio() == DEFAULT_MISSING_BAR_FAIL_RATIO

    monkeypatch.setenv(MISSING_BAR_FAIL_RATIO_ENV, "1.5")
    with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
        resolve_missing_bar_fail_ratio()

    monkeypatch.setenv(MISSING_BAR_FAIL_RATIO_ENV, "lots")
    with pytest.raises(ValueError, match="must be a number"):
        resolve_missing_bar_fail_ratio()


def test_job_reads_the_environment_when_not_given_a_ratio(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MISSING_BAR_FAIL_RATIO_ENV, "1")  # 1 disables the hard stop
    job, _ = _job(db_session, expected=10, received=0)
    assert job.missing_bar_fail_ratio == 1.0
    assert job.missing_bar_fail_ratio > module.MISSING_BAR_SLA_RATIO

    job.finalize_cycle(CYCLE_TS)  # 100% missing, no raise


def test_late_bars_count_as_received_and_warn_once_per_cycle(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Late bars are stored and received (no false missing-bar record); the cycle logs
    one summary warning naming the late symbols."""
    job, audit = _job(db_session, expected=4, received=4)
    job.late_symbols = {"S01", "S03"}

    with caplog.at_level("WARNING", logger=module.logger.name):
        job.finalize_cycle(CYCLE_TS)

    assert audit.missing == []
    assert audit.sla_breaches == []
    late = [r for r in caplog.records if r.getMessage() == "ingest_bars.late_bars"]
    assert len(late) == 1
    assert late[0].late_symbols == ["S01", "S03"]  # type: ignore[attr-defined]
    assert late[0].late_count == 2  # type: ignore[attr-defined]
