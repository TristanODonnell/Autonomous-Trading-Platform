from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.services.orphan_job_recovery_service import (
    OrphanJobRecoveryService,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def _seed_job_run(
    db_session: Session,
    *,
    status: str,
    started_at: datetime,
    job_name: str = "test_job",
) -> RuntimeJobRuns:
    row = RuntimeJobRuns(
        job_run_id=str(uuid4()),
        job_name=job_name,
        parent_job_run_id=None,
        status=status,
        trigger_type="scheduled",
        started_at=started_at,
        completed_at=None,
        duration_ms=None,
        error_message=None,
        correlation_id=str(uuid4()),
        input_summary_json=None,
        output_summary_json=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_rescue_marks_stale_running_job_as_failed(db_session: Session) -> None:
    cutoff = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    old_start = cutoff - timedelta(hours=2)
    row = _seed_job_run(db_session, status="running", started_at=old_start)

    service = OrphanJobRecoveryService(db_session)
    rescued = service.rescue_orphan_running_jobs(cutoff=cutoff)

    assert len(rescued) == 1
    assert rescued[0].job_run_id == row.job_run_id
    assert rescued[0].status == "failed"
    assert rescued[0].completed_at is not None
    assert rescued[0].duration_ms is not None
    assert rescued[0].duration_ms >= 0
    assert rescued[0].error_message is not None and "orphan" in rescued[0].error_message


def test_rescue_does_not_touch_recently_started_running_job(db_session: Session) -> None:
    cutoff = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    recent_start = cutoff + timedelta(minutes=1)
    row = _seed_job_run(db_session, status="running", started_at=recent_start)

    service = OrphanJobRecoveryService(db_session)
    rescued = service.rescue_orphan_running_jobs(cutoff=cutoff)

    assert rescued == []
    db_session.refresh(row)
    assert row.status == "running"


def test_rescue_returns_empty_list_when_no_stale_jobs(db_session: Session) -> None:
    cutoff = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    service = OrphanJobRecoveryService(db_session)
    rescued = service.rescue_orphan_running_jobs(cutoff=cutoff)

    assert rescued == []


def test_rescue_does_not_affect_already_completed_jobs(db_session: Session) -> None:
    cutoff = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    old_start = cutoff - timedelta(hours=1)
    _seed_job_run(db_session, status="completed", started_at=old_start)
    _seed_job_run(db_session, status="failed", started_at=old_start)

    service = OrphanJobRecoveryService(db_session)
    rescued = service.rescue_orphan_running_jobs(cutoff=cutoff)

    assert rescued == []


def test_rescue_handles_multiple_orphan_jobs(db_session: Session) -> None:
    cutoff = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    old_start = cutoff - timedelta(hours=1)
    r1 = _seed_job_run(db_session, status="running", started_at=old_start, job_name="job_a")
    r2 = _seed_job_run(db_session, status="running", started_at=old_start, job_name="job_b")

    service = OrphanJobRecoveryService(db_session)
    rescued = service.rescue_orphan_running_jobs(cutoff=cutoff)

    assert len(rescued) == 2
    rescued_ids = {r.job_run_id for r in rescued}
    assert r1.job_run_id in rescued_ids
    assert r2.job_run_id in rescued_ids
    for r in rescued:
        assert r.status == "failed"
