from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

import autonomous_trading_platform.scheduler.cycles.run_governance_promotion_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.scheduler.cycles.run_governance_promotion_cycle import (
    run_governance_promotion_cycle,
)
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from tests.application.services.test_auto_promotion_service import (
    _seed_candidate,
    _seed_rule,
)


def test_governance_promotion_cycle_skips_when_flag_false(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": False},
        updated_by="test",
    )

    result = run_governance_promotion_cycle(datetime(2026, 5, 13, tzinfo=UTC))

    assert result["skipped_reason"] == "auto_promote_disabled"
    job = db_session.query(RuntimeJobRuns).filter_by(job_name="strategy_auto_promotion_cycle").one()
    assert job.status == "completed"
    assert job.output_summary_json["skipped_reason"] == "auto_promote_disabled"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.run_type == RunType.GOVERNANCE
    assert manifest.artifact_manifest["governance_action"] == "auto_promotion"


def test_governance_promotion_cycle_runs_when_flag_true(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    telemetry_events: list[str] = []
    monkeypatch.setattr(
        cycle_module,
        "record_cycle_started",
        lambda **kwargs: telemetry_events.append("started"),
    )
    monkeypatch.setattr(
        cycle_module,
        "record_cycle_completed",
        lambda **kwargs: telemetry_events.append("completed"),
    )
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True, "notify_strategy_promotion_events": True},
        updated_by="test",
    )

    result = run_governance_promotion_cycle(datetime(2026, 5, 13, tzinfo=UTC))

    assert result["skipped_reason"] is None
    assert result["promotions_executed"][0]["strategy_id"] == "eligible"
    job = db_session.query(RuntimeJobRuns).filter_by(job_name="strategy_auto_promotion_cycle").one()
    assert job.status == "completed"
    assert job.output_summary_json["audit_event_emitted"] == "STRATEGY_AUTO_PROMOTION_COMPLETED"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.status == "completed"
    assert manifest.artifact_manifest["strategy_ids"] == ["eligible"]
    assert telemetry_events == ["started", "completed"]


def _patch_cycle_session(monkeypatch, session: Session) -> None:
    monkeypatch.setattr(cycle_module, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
