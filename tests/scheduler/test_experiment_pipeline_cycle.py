from typing import cast

import pytest

from autonomous_trading_platform.scheduler.cycles.run_experiment_pipeline_cycle import (
    run_experiment_pipeline_cycle,
)
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from tests.utilities.experiment_pipeline_cycle_fixture import (
    EXPERIMENT_DATASET_VERSION,
    EXPERIMENT_END_DATE,
    EXPERIMENT_START_DATE,
    EXPERIMENT_YAML_CONTENT,
)

# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _latest_runtime_job_run(db_session, job_name: str) -> RuntimeJobRuns | None:
    return cast(
        RuntimeJobRuns | None,
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == job_name)
        .order_by(RuntimeJobRuns.started_at.desc())
        .first(),
    )


def _latest_manifest(db_session) -> RunManifestRow | None:
    return cast(
        RunManifestRow | None,
        db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first(),
    )


# ---------------------------------------------------------------------------
# Happy path — non-staged
# ---------------------------------------------------------------------------


def test_experiment_pipeline_cycle_runs_successfully(
    seeded_experiment_pipeline_cycle_fixture,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.experiment_plan,
        now_utc=fixture.now_utc,
    )


def test_experiment_pipeline_cycle_creates_completed_runtime_job_run(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.experiment_plan,
        now_utc=fixture.now_utc,
    )

    job_run = _latest_runtime_job_run(db_session, "experiment_pipeline_cycle")

    assert job_run is not None
    assert job_run.status == "completed"
    assert job_run.error_message is None
    assert job_run.job_name == "experiment_pipeline_cycle"


def test_experiment_pipeline_cycle_runtime_job_run_input_summary(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.experiment_plan,
        now_utc=fixture.now_utc,
    )

    job_run = _latest_runtime_job_run(db_session, "experiment_pipeline_cycle")

    assert job_run is not None
    assert job_run.input_summary_json["experiment_id"] == fixture.experiment_plan.experiment_id
    assert job_run.input_summary_json["component"] == "scheduler.run_experiment_pipeline_cycle"


def test_experiment_pipeline_cycle_runtime_job_run_output_summary(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.experiment_plan,
        now_utc=fixture.now_utc,
    )

    job_run = _latest_runtime_job_run(db_session, "experiment_pipeline_cycle")

    assert job_run is not None
    assert job_run.output_summary_json["experiment_id"] == fixture.experiment_plan.experiment_id
    assert "final_survivors_count" in job_run.output_summary_json
    assert "total_simulation_runs" in job_run.output_summary_json
    assert job_run.output_summary_json["last_successful_step"] == "run_experiment"


def test_experiment_pipeline_cycle_creates_run_manifest(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.experiment_plan,
        now_utc=fixture.now_utc,
    )

    manifest = _latest_manifest(db_session)

    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert manifest.strategy_id == fixture.experiment_plan.experiment_id
    assert manifest.start_date == EXPERIMENT_START_DATE
    assert manifest.end_date == EXPERIMENT_END_DATE


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


def test_experiment_pipeline_cycle_non_staged_dispatches_to_run_experiment(
    seeded_experiment_pipeline_cycle_fixture,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.experiment_plan,
        now_utc=fixture.now_utc,
    )

    assert len(fixture.fake_orchestration_service.run_experiment_calls) == 1
    assert fixture.fake_orchestration_service.run_staged_experiment_calls == []
    assert fixture.fake_orchestration_service.run_experiment_calls[0] is fixture.experiment_plan


def test_experiment_pipeline_cycle_staged_dispatches_to_run_staged_experiment(
    seeded_experiment_pipeline_cycle_fixture,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.staged_experiment_plan,
        now_utc=fixture.now_utc,
    )

    assert len(fixture.fake_orchestration_service.run_staged_experiment_calls) == 1
    assert fixture.fake_orchestration_service.run_experiment_calls == []
    assert (
        fixture.fake_orchestration_service.run_staged_experiment_calls[0]
        is fixture.staged_experiment_plan
    )


def test_experiment_pipeline_cycle_staged_job_run_is_completed(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture

    run_experiment_pipeline_cycle(
        experiment_plan=fixture.staged_experiment_plan,
        now_utc=fixture.now_utc,
    )

    job_run = _latest_runtime_job_run(db_session, "experiment_pipeline_cycle")

    assert job_run is not None
    assert job_run.status == "completed"
    assert (
        job_run.output_summary_json["experiment_id"] == fixture.staged_experiment_plan.experiment_id
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_experiment_pipeline_cycle_marks_job_run_failed_on_error(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture
    fixture.fake_orchestration_service.configure_to_raise(
        RuntimeError("simulated experiment failure")
    )

    with pytest.raises(RuntimeError, match="simulated experiment failure"):
        run_experiment_pipeline_cycle(
            experiment_plan=fixture.experiment_plan,
            now_utc=fixture.now_utc,
        )

    job_run = _latest_runtime_job_run(db_session, "experiment_pipeline_cycle")

    assert job_run is not None
    assert job_run.status == "failed"
    assert "simulated experiment failure" in job_run.error_message


def test_experiment_pipeline_cycle_marks_manifest_failed_on_error(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_experiment_pipeline_cycle_fixture
    fixture.fake_orchestration_service.configure_to_raise(
        RuntimeError("simulated experiment failure")
    )

    with pytest.raises(RuntimeError):
        run_experiment_pipeline_cycle(
            experiment_plan=fixture.experiment_plan,
            now_utc=fixture.now_utc,
        )

    manifest = _latest_manifest(db_session)

    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.error_message is not None


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_experiment_pipeline_cycle_raises_without_plan_or_config(
    seeded_experiment_pipeline_cycle_fixture,
):
    with pytest.raises(ValueError, match="Either experiment_plan or config_path must be provided"):
        run_experiment_pipeline_cycle()


def test_experiment_pipeline_cycle_raises_with_both_plan_and_config(
    seeded_experiment_pipeline_cycle_fixture,
    tmp_path,
):
    fixture = seeded_experiment_pipeline_cycle_fixture
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(EXPERIMENT_YAML_CONTENT)

    with pytest.raises(ValueError, match="Provide only one of"):
        run_experiment_pipeline_cycle(
            experiment_plan=fixture.experiment_plan,
            config_path=str(yaml_path),
        )


# ---------------------------------------------------------------------------
# YAML config path loading
# ---------------------------------------------------------------------------


def test_experiment_pipeline_cycle_loads_plan_from_yaml(
    seeded_experiment_pipeline_cycle_fixture,
    db_session,
    tmp_path,
):
    fixture = seeded_experiment_pipeline_cycle_fixture
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(EXPERIMENT_YAML_CONTENT)

    run_experiment_pipeline_cycle(
        config_path=str(yaml_path),
        now_utc=fixture.now_utc,
    )

    job_run = _latest_runtime_job_run(db_session, "experiment_pipeline_cycle")

    assert job_run is not None
    assert job_run.status == "completed"
    assert job_run.input_summary_json["config_path"] == str(yaml_path)
    assert job_run.output_summary_json["experiment_id"] == "test_yaml_experiment"


def test_experiment_pipeline_cycle_yaml_sets_correct_experiment_fields(
    seeded_experiment_pipeline_cycle_fixture,
    tmp_path,
):
    fixture = seeded_experiment_pipeline_cycle_fixture
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(EXPERIMENT_YAML_CONTENT)

    run_experiment_pipeline_cycle(
        config_path=str(yaml_path),
        now_utc=fixture.now_utc,
    )

    assert len(fixture.fake_orchestration_service.run_experiment_calls) == 1
    loaded_plan = fixture.fake_orchestration_service.run_experiment_calls[0]

    assert loaded_plan.experiment_id == "test_yaml_experiment"
    assert loaded_plan.dataset_version == EXPERIMENT_DATASET_VERSION
    assert loaded_plan.price_basis.value == "raw"
    assert loaded_plan.start_date == EXPERIMENT_START_DATE
    assert loaded_plan.end_date == EXPERIMENT_END_DATE
    assert loaded_plan.staged_pipeline_config is None
