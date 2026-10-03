from datetime import date, timedelta

import pytest

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
    monkeypatch,
):
    fixture = seeded_corporate_action_ingestion_cycle_fixture

    captured_jobs = []

    class CapturingFakeIngestCorporateActionsJob:
        def __init__(self, **kwargs):
            captured_jobs.append(kwargs)

        def ingest_corporate_actions_job(self):
            return None

    monkeypatch.setattr(
        cycle_module,
        "IngestCorporateActionsJob",
        CapturingFakeIngestCorporateActionsJob,
    )

    run_corporate_action_ingestion_cycle()

    assert captured_jobs != []

    job_kwargs = captured_jobs[0]

    assert (
        job_kwargs["source_raw_bars_dataset_version_id"] == fixture.source_raw_bars_dataset_version
    )
    assert job_kwargs["dataset_version_id"].startswith("corporate_actions_")
    assert "adjusted_bars_dataset_version_id" not in job_kwargs
    assert job_kwargs["ingestion_run_id"] is not None


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
    assert "adjusted_bars_dataset_version_id" not in job.output_summary_json
    assert (
        job.output_summary_json["corporate_actions_dataset_version_id"]
        == job.output_summary_json["dataset_version_id"]
    )
    as_of = date.fromisoformat(job.output_summary_json["as_of"])
    assert date.fromisoformat(job.output_summary_json["fetch_start"]) == as_of - timedelta(days=7)
    assert date.fromisoformat(job.output_summary_json["fetch_end"]) == as_of + timedelta(days=30)
    assert job.output_summary_json["counts"] == {}

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


def test_corporate_action_ingestion_cycle_marks_failure_when_parquet_write_fails(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
    monkeypatch,
):
    class FailingIngestCorporateActionsJob:
        def __init__(self, **kwargs):
            pass

        def ingest_corporate_actions_job(self):
            raise OSError("simulated parquet write failure")

    monkeypatch.setattr(
        cycle_module,
        "IngestCorporateActionsJob",
        FailingIngestCorporateActionsJob,
    )

    with pytest.raises(OSError, match="simulated parquet write failure"):
        run_corporate_action_ingestion_cycle()

    ingestion_run = _latest_ingestion_run(db_session)
    manifest = _latest_manifest(db_session)
    job = _latest_runtime_job_run(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "failed"
    assert "simulated parquet write failure" in ingestion_run.error_message

    assert manifest is not None
    assert manifest.status == "failed"
    assert "simulated parquet write failure" in manifest.error_message

    assert job is not None
    assert job.status == "failed"
    assert "simulated parquet write failure" in job.error_message


def _capture_job_kwargs(monkeypatch):
    captured = []

    class CapturingJob:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def ingest_corporate_actions_job(self):
            return None

    monkeypatch.setattr(cycle_module, "IngestCorporateActionsJob", CapturingJob)
    return captured


def test_fetch_window_defaults_to_seven_days_back_and_thirty_ahead_of_as_of(
    seeded_corporate_action_ingestion_cycle_fixture,
    monkeypatch,
):
    captured = _capture_job_kwargs(monkeypatch)

    summary = run_corporate_action_ingestion_cycle(as_of=date(2024, 6, 10))

    assert captured[0]["fetch_start"] == "2024-06-03"
    assert captured[0]["fetch_end"] == "2024-07-10"
    assert summary["as_of"] == "2024-06-10"
    assert summary["fetch_start"] == "2024-06-03"
    assert summary["fetch_end"] == "2024-07-10"
    assert summary["cycle_start"].startswith("2024-06-03")
    assert summary["cycle_end"].startswith("2024-07-10")


def test_explicit_fetch_dates_override_the_default_window(
    seeded_corporate_action_ingestion_cycle_fixture,
    monkeypatch,
):
    captured = _capture_job_kwargs(monkeypatch)

    run_corporate_action_ingestion_cycle(
        as_of=date(2024, 6, 10), fetch_start="2024-01-01", fetch_end="2024-06-30"
    )

    assert captured[0]["fetch_start"] == "2024-01-01"
    assert captured[0]["fetch_end"] == "2024-06-30"


def test_inverted_fetch_window_is_rejected(
    seeded_corporate_action_ingestion_cycle_fixture,
    monkeypatch,
):
    _capture_job_kwargs(monkeypatch)
    with pytest.raises(ValueError, match="inverted"):
        run_corporate_action_ingestion_cycle(fetch_start="2024-06-30", fetch_end="2024-06-01")


def test_fetch_symbols_default_to_the_source_dataset_manifest(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
    monkeypatch,
):
    fixture = seeded_corporate_action_ingestion_cycle_fixture
    row = db_session.get(DatasetVersions, fixture.source_raw_bars_dataset_version)
    row.source_manifest = {**(row.source_manifest or {}), "symbols": ["nvda", "AAPL", "NVDA"]}
    db_session.flush()
    captured = _capture_job_kwargs(monkeypatch)

    summary = run_corporate_action_ingestion_cycle(
        source_raw_bars_dataset_version_id=fixture.source_raw_bars_dataset_version
    )

    assert captured[0]["fetch_symbols"] == ["AAPL", "NVDA"]
    assert summary["fetch_symbol_count"] == 2


def test_explicit_fetch_symbols_win_over_the_manifest(
    seeded_corporate_action_ingestion_cycle_fixture,
    monkeypatch,
):
    captured = _capture_job_kwargs(monkeypatch)

    run_corporate_action_ingestion_cycle(fetch_symbols=["MSFT"])

    assert captured[0]["fetch_symbols"] == ["MSFT"]


def test_job_counts_are_surfaced_in_the_summary_and_runtime_job_run(
    seeded_corporate_action_ingestion_cycle_fixture,
    db_session,
    monkeypatch,
):
    from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
        CorporateActionIngestionCounts,
        CorporateActionProcessingResult,
    )

    class CountingJob:
        def __init__(self, **kwargs):
            pass

        def ingest_corporate_actions_job(self):
            return CorporateActionProcessingResult(
                created_actions=[],
                counts=CorporateActionIngestionCounts(fetched=4, created=3, manual_review=1),
            )

    monkeypatch.setattr(cycle_module, "IngestCorporateActionsJob", CountingJob)

    summary = run_corporate_action_ingestion_cycle()

    assert summary["counts"]["created"] == 3
    assert summary["counts"]["manual_review"] == 1
    job = _latest_runtime_job_run(db_session)
    assert job.output_summary_json["counts"]["fetched"] == 4
    assert _latest_ingestion_run(db_session).row_count == 3
