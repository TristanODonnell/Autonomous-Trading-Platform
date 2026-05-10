from __future__ import annotations

import pytest

from autonomous_trading_platform.execution.errors import OrderNotAllowedForSubmissionError
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def test_paper_runtime_order_safeguard_failure_is_persisted_and_audited(
    db_session,
    seeded_paper_trading_cycle_fixture,
    monkeypatch,
) -> None:
    fixture = seeded_paper_trading_cycle_fixture
    monkeypatch.setenv("PAPER_RUNTIME_ORDER_SAFEGUARDS_ENABLED", "true")
    monkeypatch.setenv("PAPER_RUNTIME_MAX_ORDER_QTY", "1")
    monkeypatch.setenv("PAPER_RUNTIME_MAX_ORDER_NOTIONAL", "1.00")
    monkeypatch.setenv("PAPER_RUNTIME_MAX_LIMIT_PRICE", "1.00")
    monkeypatch.setenv("PAPER_RUNTIME_ALLOWED_SYMBOLS", fixture.symbol)
    monkeypatch.setenv("PAPER_RUNTIME_REQUIRE_LIMIT_ORDERS", "true")

    with pytest.raises(OrderNotAllowedForSubmissionError):
        run_trading_cycle(now_utc=fixture.now_utc)

    runtime_job = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "trading_cycle")
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )
    assert runtime_job is not None
    assert runtime_job.status == "failed"
    assert runtime_job.completed_at is not None
    assert runtime_job.error_message is not None
    assert "paper runtime safeguard rejected order" in runtime_job.error_message
    assert runtime_job.output_summary_json["current_step"] == "order_submission"

    safeguard_event = (
        db_session.query(AuditLogRow)
        .filter(AuditLogRow.run_id == runtime_job.correlation_id)
        .filter(AuditLogRow.event_type == "ORDER_REJECTED_BY_SAFEGUARD")
        .first()
    )
    assert safeguard_event is not None
    assert safeguard_event.event_metadata is not None
    assert safeguard_event.event_metadata["severity"] == "warning"
