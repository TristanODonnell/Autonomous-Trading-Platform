import autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.storage.parquet.datasets import CORPORATE_ACTIONS_DATASET
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def _latest_runtime_job_run(db_session):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "corporate_action_ingestion_cycle")
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )


def _latest_ingestion_run(db_session):
    return db_session.query(IngestionRuns).order_by(IngestionRuns.created_at.desc()).first()


def _latest_dataset_version(db_session):
    return db_session.query(DatasetVersions).order_by(DatasetVersions.created_at.desc()).first()


def _latest_manifest(db_session):
    return db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()


def test_corporate_action_ingestion_cycle_runs_successfully(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
):
    run_corporate_action_ingestion_cycle()

    ingestion_run = _latest_ingestion_run(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "completed"
    assert ingestion_run.error_message is None
    assert ingestion_run.completed_at is not None


def test_corporate_action_dataset_version_is_created_and_validated(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
):
    run_corporate_action_ingestion_cycle()

    dataset_version = _latest_dataset_version(db_session)

    assert dataset_version is not None
    assert dataset_version.dataset_name == "corporate_actions"
    assert dataset_version.source == "alpaca"
    assert (
        dataset_version.source_manifest["source_raw_bars_dataset_version_id"]
        == seeded_corporate_action_ingestion_cycle_fixture.source_raw_bars_dataset_version
    )
    assert dataset_version.price_basis == PriceBasis.RAW
    assert dataset_version.interval == BarInterval.ONE_DAY
    assert dataset_version.schema_version == CORPORATE_ACTIONS_DATASET.schema_version
    assert dataset_version.validation_status == "validated"
    assert dataset_version.source_manifest is not None
    assert dataset_version.source_manifest["pipeline"] == "corporate_actions_ingestion"


def test_corporate_action_manifest_is_completed(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
):
    run_corporate_action_ingestion_cycle()

    manifest = _latest_manifest(db_session)

    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert manifest.strategy_id == "baseline_strategy"
    assert manifest.interval == BarInterval.ONE_DAY
    assert manifest.dataset_version is not None


def test_corporate_action_job_receives_source_raw_dataset_version(
    seeded_corporate_action_ingestion_cycle_fixture,
):
    fixture = seeded_corporate_action_ingestion_cycle_fixture

    captured_jobs = []

    class CapturingFakeIngestCorporateActionsJob:
        def __init__(self, **kwargs):
            captured_jobs.append(kwargs)

        def ingest_corporate_actions_job(self):
            return None

    cycle_module.IngestCorporateActionsJob = CapturingFakeIngestCorporateActionsJob

    run_corporate_action_ingestion_cycle()

    assert captured_jobs != []

    job_kwargs = captured_jobs[0]

    assert (
        job_kwargs["source_raw_bars_dataset_version_id"] == fixture.source_raw_bars_dataset_version
    )
    assert job_kwargs["dataset_version_id"].startswith("corporate_actions_")
    assert job_kwargs["adjusted_bars_dataset_version_id"].startswith("adjusted_bars_")
    assert job_kwargs["dataset_version_id"] != job_kwargs["adjusted_bars_dataset_version_id"]
    assert job_kwargs["ingestion_run_id"] is not None
    assert job_kwargs["bar_repository"] is not None


def test_activity_events_are_emitted_for_corporate_action_ingestion_cycle(
    seeded_corporate_action_ingestion_cycle_fixture,
    monkeypatch,
):
    recorded_events = []

    class FakeAuditLogger:
        def __init__(self, session):
            pass

        def record_run_started(self, **kwargs):
            recorded_events.append(("started", kwargs))

        def record_run_completed(self, **kwargs):
            recorded_events.append(("completed", kwargs))

        def record_run_failed(self, **kwargs):
            recorded_events.append(("failed", kwargs))

    monkeypatch.setattr(
        cycle_module,
        "AuditLoggingService",
        FakeAuditLogger,
    )

    run_corporate_action_ingestion_cycle()

    assert recorded_events != []
    assert any(event[0] == "started" for event in recorded_events)
    assert any(event[0] == "completed" for event in recorded_events)
    assert not any(event[0] == "failed" for event in recorded_events)

    completed_event = next(event for event in recorded_events if event[0] == "completed")
    completed_metadata = completed_event[1]["metadata"]

    assert completed_metadata["pipeline"] == "corporate_actions_ingestion"
    assert completed_metadata["dataset_version_id"] is not None
    assert completed_metadata["ingestion_run_id"] is not None


def test_runtime_job_run_is_recorded_for_corporate_action_ingestion_cycle(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_corporate_action_ingestion_cycle_fixture

    run_corporate_action_ingestion_cycle()

    ingestion_run = _latest_ingestion_run(db_session)
    job = _latest_runtime_job_run(db_session)

    assert ingestion_run is not None
    assert job is not None

    assert job.output_summary_json["corporate_actions_dataset_version_id"] is not None
    assert job.output_summary_json["adjusted_bars_dataset_version_id"] is not None
    assert (
        job.output_summary_json["corporate_actions_dataset_version_id"]
        == job.output_summary_json["dataset_version_id"]
    )
    assert (
        job.output_summary_json["corporate_actions_dataset_version_id"]
        != job.output_summary_json["adjusted_bars_dataset_version_id"]
    )

    assert job.job_name == "corporate_action_ingestion_cycle"
    assert job.parent_job_run_id is None
    assert job.status == "completed"
    assert job.trigger_type == "scheduler"
    assert job.error_message is None
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.duration_ms is not None
    assert job.duration_ms >= 0

    assert job.input_summary_json is not None
    assert job.input_summary_json["component"] == "scheduler.run_corporate_action_ingestion_cycle"
    assert job.input_summary_json["dataset_name"] == CORPORATE_ACTIONS_DATASET.dataset_key
    assert job.input_summary_json["price_basis"] == PriceBasis.RAW.value
    assert job.input_summary_json["interval"] == BarInterval.ONE_DAY.value

    assert job.output_summary_json is not None
    assert job.output_summary_json["ingestion_run_id"] == str(ingestion_run.ingestion_run_id)
    assert (
        job.output_summary_json["source_raw_bars_dataset_version_id"]
        == fixture.source_raw_bars_dataset_version
    )
    assert job.output_summary_json["last_successful_step"] == "ingest_corporate_actions"
