from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from tests.conftest import auth_headers


def seed_operations_state(db_session: Session) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            RuntimeJobRuns(
                job_run_id="trading-run-old",
                job_name="trading_cycle",
                parent_job_run_id=None,
                status="completed",
                trigger_type="schedule",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=9),
                duration_ms=60000,
                error_message=None,
                correlation_id="corr-old",
                input_summary_json={"mode": "paper"},
                output_summary_json={"orders": 2},
            ),
            RuntimeJobRuns(
                job_run_id="trading-run-new",
                job_name="trading_cycle",
                parent_job_run_id=None,
                status="failed",
                trigger_type="manual",
                started_at=now,
                completed_at=now + timedelta(minutes=1),
                duration_ms=60000,
                error_message="simulated failure",
                correlation_id="corr-new",
                input_summary_json=None,
                output_summary_json={"orders": 0},
            ),
            RuntimeJobRuns(
                job_run_id="ingestion-run",
                job_name="market_ingestion_cycle",
                parent_job_run_id=None,
                status="completed",
                trigger_type="schedule",
                started_at=now - timedelta(minutes=5),
                completed_at=now - timedelta(minutes=4),
                duration_ms=60000,
                error_message=None,
                correlation_id="corr-ingestion",
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


def test_operations_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/operations/jobs").status_code == 401
    assert client.get("/api/v1/operations/jobs/trading_cycle/runs").status_code == 401
    assert client.get("/api/v1/operations/runtime-state").status_code == 401


def test_operations_endpoints_require_operator_or_admin(client: TestClient) -> None:
    headers = auth_headers(role="researcher")

    assert client.get("/api/v1/operations/jobs", headers=headers).status_code == 403
    assert (
        client.get(
            "/api/v1/operations/jobs/trading_cycle/runs",
            headers=headers,
        ).status_code
        == 403
    )
    assert client.get("/api/v1/operations/runtime-state", headers=headers).status_code == 403


def test_operations_jobs_reflect_runtime_job_runs(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_operations_state(db_session)

    response = client.get("/api/v1/operations/jobs", headers=auth_headers(role="operator"))

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    jobs = body["data"]["jobs"]
    assert {job["job_name"] for job in jobs} == {
        "trading_cycle",
        "market_ingestion_cycle",
    }

    trading_job = next(job for job in jobs if job["job_name"] == "trading_cycle")
    assert trading_job["latest_status"] == "failed"
    assert trading_job["run_count"] == 2
    assert isinstance(trading_job["last_started_at"], str)
    assert trading_job["last_error_message"] == "simulated failure"


def test_operations_job_runs_reflect_selected_job(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_operations_state(db_session)

    response = client.get(
        "/api/v1/operations/jobs/trading_cycle/runs?limit=1",
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["job_name"] == "trading_cycle"
    assert len(data["runs"]) == 1

    run = data["runs"][0]
    assert run["job_run_id"] == "trading-run-new"
    assert run["job_name"] == "trading_cycle"
    assert run["status"] == "failed"
    assert run["trigger_type"] == "manual"
    assert run["correlation_id"] == "corr-new"
    assert "ratp.correlation_id" in run["tempo_link"]
    assert "correlation_id" in run["loki_link"]
    assert run["output_summary"] == {"orders": 0}


def test_operations_runtime_state_reflects_control_state(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_operations_state(db_session)

    response = client.get(
        "/api/v1/operations/runtime-state",
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["trading_enabled"] is False
    assert data["trading_paused"] is True
    assert data["kill_switch_enabled"] is False
    assert data["trading_mode"] == "paper"
    assert data["reason"] == "operator pause"
    assert data["updated_by"] == "operator-user"
    assert isinstance(data["updated_at"], str)


def test_operations_job_runs_reject_invalid_limit(client: TestClient) -> None:
    response = client.get(
        "/api/v1/operations/jobs/trading_cycle/runs?limit=0",
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 422
