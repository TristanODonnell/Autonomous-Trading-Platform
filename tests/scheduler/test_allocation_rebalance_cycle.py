from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

import autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle import (
    run_allocation_rebalance_cycle,
)
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from tests.application.services.test_quality_based_reallocation_service import (
    _seed_policy,
    _seed_strategy,
)


def test_allocation_rebalance_cycle_skips_when_flag_false(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": False},
        updated_by="test",
    )

    result = run_allocation_rebalance_cycle(datetime(2026, 5, 13, tzinfo=UTC))

    assert result["skipped_reason"] == "auto_rebalance_disabled"
    job = (
        db_session.query(RuntimeJobRuns)
        .filter_by(job_name="strategy_allocation_rebalance_cycle")
        .one()
    )
    assert job.status == "completed"
    assert job.output_summary_json["skipped_reason"] == "auto_rebalance_disabled"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.run_type == RunType.GOVERNANCE
    assert manifest.artifact_manifest["governance_action"] == "allocation_rebalance"


def test_allocation_rebalance_cycle_runs_when_flag_true(
    db_session: Session,
    monkeypatch,
) -> None:
    _patch_cycle_session(monkeypatch, db_session)
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True},
        updated_by="test",
    )

    result = run_allocation_rebalance_cycle(datetime(2026, 5, 13, tzinfo=UTC))

    assert result["skipped_reason"] is None
    assert result["proposals"] != []
    job = (
        db_session.query(RuntimeJobRuns)
        .filter_by(job_name="strategy_allocation_rebalance_cycle")
        .one()
    )
    assert job.status == "completed"
    assert job.output_summary_json["audit_event_emitted"] == "STRATEGY_ALLOCATION_REBALANCED"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.status == "completed"
    assert manifest.artifact_manifest["output_decisions"]["audit_event_emitted"] == (
        "STRATEGY_ALLOCATION_REBALANCED"
    )


def _patch_cycle_session(monkeypatch, session: Session) -> None:
    monkeypatch.setattr(cycle_module, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
