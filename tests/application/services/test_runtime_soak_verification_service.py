from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_soak_verification_service import (
    RuntimeSoakVerificationService,
)
from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakCheckName,
    RuntimeSoakStatus,
    RuntimeSoakVerificationReport,
)
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_run_manifest,
)
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)


def _build_service(
    db_session: Session, *, stale_after: timedelta = timedelta(minutes=15)
) -> RuntimeSoakVerificationService:
    return RuntimeSoakVerificationService(
        session=db_session,
        environment="paper",
        stale_after=stale_after,
    )


def _window() -> tuple[datetime, datetime]:
    window_start = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=30)
    return window_start, window_end


def _seed_runtime_job_run(
    db_session: Session,
    *,
    job_name: str,
    status: str,
    started_at: datetime,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    error_message: str | None = None,
) -> RuntimeJobRuns:
    row = RuntimeJobRuns(
        job_run_id=str(uuid4()),
        job_name=job_name,
        parent_job_run_id=None,
        status=status,
        trigger_type="scheduled",
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        error_message=error_message,
        correlation_id=str(uuid4()),
        input_summary_json=None,
        output_summary_json=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_completed_trading_manifest(
    db_session: Session,
    *,
    created_at: datetime,
    last_successful_step: str = "risk_snapshot",
    status: str = "completed",
) -> RunManifestRow:
    manifest = build_trading_run_manifest(
        run_id=uuid4(),
        now_utc=created_at,
        cycle_start=created_at - timedelta(minutes=5),
        cycle_end=created_at,
    )
    manifest.created_at = created_at
    manifest.status = status
    manifest.current_step = last_successful_step
    manifest.last_successful_step = last_successful_step

    repository = RunManifestRepository(db_session)
    return repository.add(manifest)


def _seed_healthy_runtime_window(db_session: Session) -> None:
    window_start, window_end = _window()
    for index, job_name in enumerate(
        (
            "market_ingestion_cycle",
            "feature_pipeline_cycle",
            "trading_cycle",
        )
    ):
        started_at = window_start + timedelta(minutes=5 + index)
        completed_at = started_at + timedelta(minutes=1)
        _seed_runtime_job_run(
            db_session,
            job_name=job_name,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=60_000,
        )

    _seed_completed_trading_manifest(
        db_session,
        created_at=window_end - timedelta(minutes=1),
    )


def test_verify_returns_runtime_soak_report(db_session: Session) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert isinstance(report, RuntimeSoakVerificationReport)
    assert report.checks
    assert report.summary["total_checks"] == len(report.checks)
    assert report.window_start == window_start
    assert report.window_end == window_end


def test_failed_check_escalates_report_status(
    db_session: Session,
    monkeypatch,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    def _failing_check(*, window_start: datetime, window_end: datetime):
        return service._failed(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness failed for test coverage.",
        )

    monkeypatch.setattr(service, "_check_data_freshness", _failing_check)
    monkeypatch.setattr(
        service,
        "_check_observability_signals",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signals forced to pass for failure aggregation coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_failure_controls",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure controls forced to pass for failure aggregation coverage.",
        ),
    )

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert report.status == RuntimeSoakStatus.FAILED
    assert report.summary["failed"] == 1
    assert len(report.failed_checks) == 1
    assert report.failed_checks[0].check_name == RuntimeSoakCheckName.DATA_FRESHNESS


def test_warning_check_produces_warning_report_status(
    db_session: Session,
    monkeypatch,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    def _warning_check(*, window_start: datetime, window_end: datetime):
        return service._warning(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness warning for test coverage.",
        )

    monkeypatch.setattr(service, "_check_data_freshness", _warning_check)
    monkeypatch.setattr(
        service,
        "_check_observability_signals",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signals forced to pass for warning aggregation coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_failure_controls",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure controls forced to pass for warning aggregation coverage.",
        ),
    )

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert report.status == RuntimeSoakStatus.WARNING
    assert report.summary["failed"] == 0
    assert len(report.failed_checks) == 0
    assert report.summary["warnings"] == 1


def test_all_passed_checks_produce_passed_report(
    db_session: Session,
    monkeypatch,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    monkeypatch.setattr(
        service,
        "_check_observability_signals",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signals forced to pass for passed-report coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_failure_controls",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure controls forced to pass for passed-report coverage.",
        ),
    )

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert report.status == RuntimeSoakStatus.PASSED
    assert report.summary["failed"] == 0
    assert report.summary["warnings"] == 0


def test_runtime_job_health_fails_when_expected_jobs_are_missing(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    check = service._check_runtime_job_health(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.check_name == RuntimeSoakCheckName.RUNTIME_JOB_HEALTH
    assert check.metadata["missing_jobs"] == [
        "market_ingestion_cycle",
        "feature_pipeline_cycle",
        "trading_cycle",
    ]


def test_runtime_job_health_passes_when_expected_jobs_complete_successfully(
    db_session: Session,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    check = service._check_runtime_job_health(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["missing_jobs"] == []
    assert check.metadata["missing_trading_steps"] == []


def test_runtime_job_health_warns_when_failed_job_recovers_later_in_window(
    db_session: Session,
) -> None:
    _seed_healthy_runtime_window(db_session)
    window_start, window_end = _window()
    failed_started_at = window_start + timedelta(minutes=2)
    _seed_runtime_job_run(
        db_session,
        job_name="feature_pipeline_cycle",
        status="failed",
        started_at=failed_started_at,
        completed_at=failed_started_at + timedelta(minutes=1),
        duration_ms=60_000,
        error_message="transient failure",
    )

    service = _build_service(db_session)
    check = service._check_runtime_job_health(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.WARNING
    assert check.metadata["recovered_failures"] == ["feature_pipeline_cycle"]


def test_stale_running_job_fails_stale_running_state_check(
    db_session: Session,
) -> None:
    _seed_healthy_runtime_window(db_session)
    window_start, window_end = _window()
    stale_started_at = window_start
    _seed_runtime_job_run(
        db_session,
        job_name="feature_pipeline_cycle",
        status="running",
        started_at=stale_started_at,
        completed_at=None,
        duration_ms=None,
    )

    service = _build_service(db_session, stale_after=timedelta(minutes=10))
    check = service._check_stale_running_state(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.check_name == RuntimeSoakCheckName.STALE_RUNNING_STATE
    assert check.metadata["stale_runtime_job_names"] == ["feature_pipeline_cycle"]
