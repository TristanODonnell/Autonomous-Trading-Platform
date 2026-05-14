from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)


def create_governance_manifest(
    *,
    session: Session,
    run_id: UUID,
    job_name: str,
    governance_action: str,
    input_settings: dict[str, Any],
    rules_used: list[dict[str, Any]] | None = None,
) -> RunManifest:
    manifest = RunManifest(
        run_id=run_id,
        run_type=RunType.GOVERNANCE,
        created_at=datetime.now(UTC),
        environment="governance",
        broker="alpaca",
        broker_account_id="governance-automation",
        strategy_id="governance_automation",
        strategy_version="1",
        strategy_config={
            "job_name": job_name,
            "governance_action": governance_action,
            "input_settings": input_settings,
            "rules_used": rules_used or [],
        },
        capital_bucket=Decimal("0"),
        interval=BarInterval.ONE_DAY,
        start_date=date.today(),
        end_date=date.today(),
        dataset_version="governance_automation",
        price_basis=PriceBasis.ADJUSTED,
        universe_version="governance_automation",
        git_commit="unknown",
        notes=f"governance automation: {governance_action}",
        status="running",
        current_step="scan",
        last_successful_step=None,
        artifact_manifest={
            "job_name": job_name,
            "governance_action": governance_action,
            "input_settings": input_settings,
            "rules_used": rules_used or [],
        },
        governance_state=GovernanceState.APPROVED_RESEARCH,
    )
    RunManifestRepository(session).upsert(manifest)
    return manifest


def complete_governance_manifest(
    *,
    session: Session,
    manifest: RunManifest,
    output_decisions: dict[str, Any],
) -> None:
    manifest.status = "completed"
    manifest.current_step = None
    manifest.last_successful_step = "completed"
    manifest.error_message = None
    manifest.artifact_manifest = {
        **(manifest.artifact_manifest or {}),
        "strategy_ids": _strategy_ids_from_output(output_decisions),
        "output_decisions": output_decisions,
    }
    RunManifestRepository(session).upsert(manifest)


def fail_governance_manifest(
    *,
    session: Session,
    manifest: RunManifest,
    error: Exception,
) -> None:
    manifest.status = "failed"
    manifest.current_step = None
    manifest.error_message = str(error)
    manifest.artifact_manifest = {
        **(manifest.artifact_manifest or {}),
        "error": str(error),
    }
    RunManifestRepository(session).upsert(manifest)


def _strategy_ids_from_output(output_decisions: dict[str, Any]) -> list[str]:
    ids: set[str] = set()
    for key in ("candidate_strategies", "proposals", "demotions_executed", "promotions_executed"):
        rows = output_decisions.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("strategy_id") is not None:
                ids.add(str(row["strategy_id"]))
    return sorted(ids)
