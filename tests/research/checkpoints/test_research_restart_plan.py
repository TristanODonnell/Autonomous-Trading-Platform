from __future__ import annotations

from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpointIdentity,
    ResearchCheckpointStatus,
    ResearchTaskType,
)
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
)


def _identity(unit_id: str) -> ResearchCheckpointIdentity:
    return ResearchCheckpointIdentity(
        experiment_id="exp-1",
        pipeline_id="pipe-1",
        stage_name="stage",
        window_role=unit_id,
        strategy_id="strat",
        config_hash="hash",
        dataset_version="bars-v1",
        price_basis="raw",
        universe_version="v1",
        task_type=ResearchTaskType.SIMULATION,
        unit_id=unit_id,
    )


def test_restart_plan_splits_completed_missing_failed_and_actions() -> None:
    completed = _identity("completed")
    failed = _identity("failed")
    missing = _identity("missing")
    service = ResearchCheckpointService()
    service.mark_completed(completed)
    service.mark_failed(failed, error_message="boom")

    plan = service.plan_restart([completed, failed, missing], dry_run=True)

    assert [unit.identity.unit_id for unit in plan.completed_units] == ["completed"]
    assert [unit.identity.unit_id for unit in plan.failed_units] == ["failed"]
    assert [unit.identity.unit_id for unit in plan.missing_units] == ["missing"]
    assert {unit.identity.unit_id for unit in plan.units_to_rerun} == {"failed", "missing"}
    assert [unit.identity.unit_id for unit in plan.units_to_skip] == ["completed"]


def test_restart_plan_failed_only_skips_missing_units() -> None:
    failed = _identity("failed")
    missing = _identity("missing")
    service = ResearchCheckpointService()
    service.mark_failed(failed, error_message="boom")

    plan = service.plan_restart(
        [failed, missing],
        resume_failed=True,
        resume_missing=False,
    )

    assert [unit.identity.unit_id for unit in plan.units_to_rerun] == ["failed"]
    assert [unit.identity.unit_id for unit in plan.units_to_skip] == ["missing"]


def test_force_rerun_reruns_completed_units() -> None:
    completed = _identity("completed")
    service = ResearchCheckpointService()
    service.mark_completed(completed)

    plan = service.plan_restart([completed], force_rerun=True)

    assert plan.completed_units[0].status == ResearchCheckpointStatus.COMPLETED
    assert [unit.identity.unit_id for unit in plan.units_to_rerun] == ["completed"]
