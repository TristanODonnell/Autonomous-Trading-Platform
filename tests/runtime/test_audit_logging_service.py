from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from autonomous_trading_platform.runtime.services.audit_logging_service import (
    AuditLoggingService,
)


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self.add_calls: list[Any] = []

    def add(self, event: Any) -> None:
        self.add_calls.append(event)


class FakeSession:
    def __init__(self) -> None:
        self.begin_called = 0
        self.commit_called = 0
        self.rollback_called = 0

    def begin(self) -> None:
        self.begin_called += 1

    def commit(self) -> None:
        self.commit_called += 1

    def rollback(self) -> None:
        self.rollback_called += 1


class FakeSorUnitOfWork:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.audit_logs = FakeAuditLogRepository()

    def __enter__(self) -> FakeSorUnitOfWork:
        self.session.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def service(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeSession,
) -> AuditLoggingService:
    monkeypatch.setattr(
        "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
        FakeSorUnitOfWork,
    )
    return AuditLoggingService(session)


def _single_written_event(session: FakeSession) -> Any:
    """
    Helper only for tests that monkeypatch SorUnitOfWork to FakeSorUnitOfWork
    and expect exactly one event to be written by a single service call.
    """
    # Not used directly because each FakeSorUnitOfWork instance is transient.
    # Tests will instead monkeypatch a capturing factory when they need access
    # to the created repository instance.
    raise NotImplementedError


class TestAuditLoggingServiceCurrentBehavior:
    def test_record_run_started_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        metadata = {"source": "scheduler", "attempt": 1}

        service.record_run_started(
            run_id="run-123",
            component="trading_cycle",
            metadata=metadata,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.run_id == "run-123"
        assert event.event_type == "RUN_STARTED"
        assert event.component == "trading_cycle"
        assert event.message == "Run started"
        assert event.metadata == metadata
        assert isinstance(event.event_id, str)
        assert event.event_id
        assert event.event_timestamp.tzinfo == UTC

        assert session.begin_called == 1
        assert session.commit_called == 1
        assert session.rollback_called == 0

    def test_record_run_completed_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)

        service.record_run_completed(
            run_id="run-123",
            component="trading_cycle",
            metadata={"status": "ok"},
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.run_id == "run-123"
        assert event.event_type == "RUN_COMPLETED"
        assert event.component == "trading_cycle"
        assert event.message == "Run completed"
        assert event.metadata == {"status": "ok"}

    def test_record_run_failed_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)

        service.record_run_failed(
            run_id="run-123",
            component="trading_cycle",
            metadata={"reason": "broker timeout"},
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "RUN_FAILED"
        assert event.component == "trading_cycle"
        assert event.message == "Run failed"
        assert event.metadata == {"reason": "broker timeout"}

    def test_record_sla_breach_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)

        service.record_sla_breach(
            run_id="run-123",
            component="market_ingestion",
            message="ingestion exceeded 30 second SLA",
            metadata={"latency_seconds": 42.7},
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "SLA_BREACH"
        assert event.component == "market_ingestion"
        assert event.message == "ingestion exceeded 30 second SLA"
        assert event.metadata == {"latency_seconds": 42.7}

    def test_record_bar_missing_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        cycle_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

        service.record_bar_missing(
            run_id="run-123",
            symbol="AAPL",
            cycle_timestamp=cycle_timestamp,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "BAR_MISSING"
        assert event.component == "market_ingestion"
        assert event.message == "Missing bar detected for AAPL"
        assert event.metadata == {
            "symbol": "AAPL",
            "cycle_timestamp": cycle_timestamp.isoformat(),
        }

    def test_record_bar_late_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        bar_end_timestamp = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)

        service.record_bar_late(
            run_id="run-123",
            symbol="MSFT",
            bar_end_timestamp=bar_end_timestamp,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "BAR_LATE"
        assert event.component == "market_ingestion"
        assert event.message == "Late bar detected for MSFT"
        assert event.metadata == {
            "symbol": "MSFT",
            "bar_end_timestamp": bar_end_timestamp.isoformat(),
        }

    def test_record_bar_outlier_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        cycle_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

        service.record_bar_outlier(
            run_id="run-123",
            symbol="TSLA",
            cycle_timestamp=cycle_timestamp,
            reference_close=Decimal("100.25"),
            observed_close=Decimal("112.75"),
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "BAR_OUTLIER"
        assert event.component == "market_ingestion"
        assert event.message == "Suspected outlier detected for TSLA"
        assert event.metadata == {
            "symbol": "TSLA",
            "cycle_timestamp": cycle_timestamp.isoformat(),
            "reference_close": "100.25",
            "observed_close": "112.75",
        }

    def test_record_corporate_action_parse_failed_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        cycle_timestamp = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)

        service.record_corporate_action_parse_failed(
            run_id="run-123",
            symbol="AAPL",
            cycle_timestamp=cycle_timestamp,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "PARSE_FAILED"
        assert event.component == "corporate_action_ingestion"
        assert event.message == "parse failed for AAPL"
        assert event.metadata == {
            "symbol": "AAPL",
            "cycle_timestamp": cycle_timestamp.isoformat(),
        }

    def test_record_corporate_action_validation_failed_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        cycle_timestamp = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)

        service.record_corporate_action_validation_failed(
            run_id="run-123",
            symbol="AAPL",
            cycle_timestamp=cycle_timestamp,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "CORPORATE_ACTION_VALIDATION_FAILED"
        assert event.component == "corporate_action_ingestion"
        assert event.message == "corporate action validation failed for AAPL"
        assert event.metadata == {
            "symbol": "AAPL",
            "cycle_timestamp": cycle_timestamp.isoformat(),
        }

    def test_record_corporate_action_adjustment_applied_constructs_expected_event_and_writes_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)
        cycle_timestamp = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)

        service.record_corporate_action_adjustment_applied(
            run_id="run-123",
            symbol="AAPL",
            cycle_timestamp=cycle_timestamp,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.event_type == "CORPORATE_ACTION_ADJUSTMENT_APPLIED"
        assert event.component == "corporate_action_ingestion"
        assert event.message == "corporate action adjustment applied for AAPL"
        assert event.metadata == {
            "symbol": "AAPL",
            "cycle_timestamp": cycle_timestamp.isoformat(),
        }

    def test_record_event_rolls_back_when_repository_add_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        class ExplodingAuditLogRepository(FakeAuditLogRepository):
            def add(self, event: Any) -> None:
                raise RuntimeError("audit log write failed")

        class ExplodingSorUnitOfWork(FakeSorUnitOfWork):
            def __init__(self, session: FakeSession) -> None:
                self.session = session
                self.audit_logs = ExplodingAuditLogRepository()

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            ExplodingSorUnitOfWork,
        )

        service = AuditLoggingService(session)

        with pytest.raises(RuntimeError, match="audit log write failed"):
            service.record_run_started(
                run_id="run-123",
                component="trading_cycle",
                metadata={"source": "scheduler"},
            )

        assert session.begin_called == 1
        assert session.commit_called == 0
        assert session.rollback_called == 1

    def test_sensitive_data_is_redacted_before_logging(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.audit_logging_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = AuditLoggingService(session)

        sensitive_metadata = {
            "symbol": "AAPL",
            "api_key": "secret-api-key",
            "access_token": "very-secret-token",
            "password": "super-secret-password",
        }

        service.record_run_failed(
            run_id="run-123",
            component="broker_adapter",
            metadata=sensitive_metadata,
        )

        assert len(captured_uows) == 1
        assert len(captured_uows[0].audit_logs.add_calls) == 1

        event = captured_uows[0].audit_logs.add_calls[0]
        assert event.metadata == {
            "symbol": "AAPL",
            "api_key": "[REDACTED]",
            "access_token": "[REDACTED]",
            "password": "[REDACTED]",
        }
