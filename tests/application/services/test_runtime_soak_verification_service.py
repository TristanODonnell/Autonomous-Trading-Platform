from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_soak_verification_service import (
    RuntimeSoakVerificationService,
)
from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakCheckName,
    RuntimeSoakStatus,
    RuntimeSoakVerificationReport,
)


def _build_service(db_session: Session) -> RuntimeSoakVerificationService:
    return RuntimeSoakVerificationService(
        session=db_session,
        environment="paper",
    )


def _window() -> tuple[datetime, datetime]:
    window_start = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=30)
    return window_start, window_end


def test_verify_returns_runtime_soak_report(db_session: Session) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert isinstance(report, RuntimeSoakVerificationReport)
    assert report.checks
    assert report.status == RuntimeSoakStatus.WARNING
    assert report.summary["total_checks"] == len(report.checks)
    assert report.window_start == window_start
    assert report.window_end == window_end


def test_failed_check_escalates_report_status(
    db_session: Session,
    monkeypatch,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    def _failing_check(*, window_start: datetime, window_end: datetime):
        return service._failed(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness failed for test coverage.",
        )

    monkeypatch.setattr(service, "_check_data_freshness", _failing_check)

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
