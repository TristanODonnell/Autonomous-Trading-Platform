from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.operations_service import (
    OperationsService,
)
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def test_operations_service_lists_job_summaries_runs_and_runtime_state(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            RuntimeJobRuns(
                job_run_id="job-run-1",
                job_name="trading_cycle",
                parent_job_run_id=None,
                status="completed",
                trigger_type="schedule",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=9),
                duration_ms=60000,
                error_message=None,
                correlation_id="corr-1",
                input_summary_json={"mode": "paper"},
                output_summary_json={"orders": 2},
            ),
            RuntimeJobRuns(
                job_run_id="job-run-2",
                job_name="trading_cycle",
                parent_job_run_id=None,
                status="failed",
                trigger_type="manual",
                started_at=now,
                completed_at=now + timedelta(minutes=1),
                duration_ms=60000,
                error_message="simulated failure",
                correlation_id="corr-2",
                input_summary_json=None,
                output_summary_json=None,
            ),
            RuntimeControlState(
                control_id="global",
                trading_enabled=False,
                trading_paused=True,
                kill_switch_enabled=False,
                trading_mode="paper",
                reason="operator pause",
                updated_by="operator-user",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.flush()

    service = OperationsService(session=db_session)

    jobs = service.list_jobs()
    runs = service.list_job_runs(job_name="trading_cycle")
    runtime_state = service.get_runtime_state()

    assert len(jobs) == 1
    assert jobs[0]["job_name"] == "trading_cycle"
    assert jobs[0]["latest_status"] == "failed"
    assert isinstance(jobs[0]["last_started_at"], datetime)
    assert isinstance(jobs[0]["last_completed_at"], datetime)
    assert jobs[0]["last_duration_ms"] == 60000
    assert jobs[0]["last_error_message"] == "simulated failure"
    assert jobs[0]["run_count"] == 2
    assert [run["job_run_id"] for run in runs] == ["job-run-2", "job-run-1"]
    assert runtime_state["trading_paused"] is True
    assert runtime_state["reason"] == "operator pause"
