from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

import autonomous_trading_platform.scheduler.cycles.run_governance_demotion_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.scheduler.cycles.run_governance_demotion_cycle import (
    run_governance_demotion_cycle,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from tests.application.services.test_auto_demotion_service import _seed_strategy


def test_governance_demotion_cycle_skips_when_flag_false(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    _seed_strategy(db_session, "breach", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": False, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = run_governance_demotion_cycle(datetime(2026, 5, 13, tzinfo=UTC))

    assert result["skipped_reason"] == "auto_demote_disabled"
    job = db_session.query(RuntimeJobRuns).filter_by(job_name="strategy_auto_demotion_cycle").one()
    assert job.status == "completed"
    assert job.output_summary_json["skipped_reason"] == "auto_demote_disabled"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.run_type == RunType.GOVERNANCE
    assert manifest.artifact_manifest["governance_action"] == "auto_demotion"


def test_governance_demotion_cycle_runs_when_flag_true(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    _seed_strategy(db_session, "breach", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = run_governance_demotion_cycle(datetime(2026, 5, 13, tzinfo=UTC))

    assert result["skipped_reason"] is None
    assert result["demotions_executed"][0]["strategy_id"] == "breach"
    job = db_session.query(RuntimeJobRuns).filter_by(job_name="strategy_auto_demotion_cycle").one()
    assert job.status == "completed"
    assert job.output_summary_json["audit_event_emitted"] == "STRATEGY_AUTO_DEMOTION_COMPLETED"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.status == "completed"
    assert manifest.artifact_manifest["strategy_ids"] == ["breach"]


def test_governance_demotion_cycle_failure_emits_pipeline_failure_when_enabled(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    OperatorSettingsRepository(db_session).update_current(
        {"notify_pipeline_failures": True},
        updated_by="test",
    )

    def explode(*args, **kwargs):
        raise RuntimeError("demotion boom")

    monkeypatch.setattr(cycle_module.AutoDemotionService, "run", explode)

    try:
        run_governance_demotion_cycle(datetime(2026, 5, 13, tzinfo=UTC))
    except RuntimeError as exc:
        assert str(exc) == "demotion boom"
    else:
        raise AssertionError("expected demotion cycle failure")

    failed_job = (
        db_session.query(RuntimeJobRuns)
        .filter_by(
            job_name="strategy_auto_demotion_cycle",
            status="failed",
        )
        .one()
    )
    manifest = (
        db_session.query(RunManifestRow).filter_by(run_id=UUID(failed_job.correlation_id)).one()
    )
    event = db_session.query(AuditLogRow).filter_by(event_type="PIPELINE_FAILURE_EVENT").one()

    assert failed_job.error_message == "demotion boom"
    assert manifest.status == "failed"
    assert manifest.error_message == "demotion boom"
    assert event.event_metadata["job_name"] == "strategy_auto_demotion_cycle"


def _patch_cycle_session(monkeypatch, session: Session) -> None:
    monkeypatch.setattr(cycle_module, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
