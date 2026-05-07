from autonomous_trading_platform.scheduler.orchestration.historical_research_golden_path_orchestrator import (
    HistoricalResearchGoldenPathOrchestrator,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def _runtime_job_runs_by_name(db_session, job_name):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == job_name)
        .order_by(RuntimeJobRuns.started_at.desc())
        .all()
    )


def _runtime_job_runs_for_correlation(db_session, correlation_id):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == correlation_id)
        .all()
    )


def _latest_backfill_raw_dataset(db_session):
    return (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == "raw_bars")
        .filter(DatasetVersions.metadata_json["dataset_type"].as_string() == "historical_backfill")
        .order_by(DatasetVersions.created_at.desc())
        .first()
    )


def _latest_corporate_action_dataset(db_session):
    return (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == "corporate_actions")
        .order_by(DatasetVersions.created_at.desc())
        .first()
    )


def _feature_dataset_versions_for_source(db_session, source_dataset_version):
    return (
        db_session.query(FeatureDatasetVersions)
        .filter(FeatureDatasetVersions.source_dataset_version == source_dataset_version)
        .all()
    )


# ---------------------------------------------------------------------------
# Full golden path — with experiment step
# ---------------------------------------------------------------------------


def test_historical_research_golden_path_runs_backfill_corporate_actions_features_experiment(
    seeded_historical_research_golden_path_fixture,
    db_session,
):
    fixture = seeded_historical_research_golden_path_fixture

    orchestrator = HistoricalResearchGoldenPathOrchestrator(db_session)

    result = orchestrator.run(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
        now_utc=fixture.now_utc,
        experiment_plan=fixture.experiment_plan,
    )

    ingestion_run = (
        db_session.query(IngestionRuns).order_by(IngestionRuns.created_at.desc()).first()
    )

    raw_dataset = _latest_backfill_raw_dataset(db_session)
    corporate_action_dataset = _latest_corporate_action_dataset(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "completed"
    assert ingestion_run.error_message is None

    assert raw_dataset is not None
    assert raw_dataset.dataset_name == "raw_bars"
    assert raw_dataset.validation_status == "validated"
    assert raw_dataset.symbol_coverage == fixture.symbol_count
    assert raw_dataset.date_coverage_start == fixture.start.date()
    assert raw_dataset.date_coverage_end == fixture.end.date()
    assert raw_dataset.metadata_json["dataset_type"] == "historical_backfill"

    assert corporate_action_dataset is not None
    assert corporate_action_dataset.dataset_name == "corporate_actions"
    assert corporate_action_dataset.validation_status == "validated"
    assert corporate_action_dataset.source_manifest["pipeline"] == "corporate_actions_ingestion"

    feature_versions = _feature_dataset_versions_for_source(
        db_session,
        raw_dataset.dataset_version_id,
    )

    assert feature_versions != []

    feature_names = {row.feature_name for row in feature_versions}

    assert "returns" in feature_names
    assert "volatility" in feature_names
    assert "moving_average" in feature_names
    assert "liquidity" in feature_names
    assert "regime" in feature_names

    backfill_jobs = _runtime_job_runs_by_name(db_session, "market_backfill_cycle")
    corporate_jobs = _runtime_job_runs_by_name(db_session, "corporate_action_ingestion_cycle")
    feature_jobs = _runtime_job_runs_by_name(db_session, "feature_pipeline_cycle")
    experiment_jobs = _runtime_job_runs_by_name(db_session, "experiment_pipeline_cycle")
    golden_path_jobs = _runtime_job_runs_by_name(db_session, "historical_research_golden_path")

    assert backfill_jobs != []
    assert corporate_jobs != []
    assert feature_jobs != []
    assert experiment_jobs != []
    assert golden_path_jobs != []

    assert backfill_jobs[0].status == "completed"
    assert corporate_jobs[0].status == "completed"
    assert feature_jobs[0].status == "completed"
    assert experiment_jobs[0].status == "completed"
    assert golden_path_jobs[0].status == "completed"

    orchestrator_jobs = _runtime_job_runs_for_correlation(
        db_session,
        result.correlation_id,
    )

    assert orchestrator_jobs != []
    assert len(orchestrator_jobs) == 1

    orchestrator_job = orchestrator_jobs[0]

    assert orchestrator_job.job_name == "historical_research_golden_path"
    assert orchestrator_job.status == "completed"
    assert orchestrator_job.input_summary_json["mode"] == "historical_research"
    assert orchestrator_job.input_summary_json["steps"] == [
        "market_backfill_cycle",
        "corporate_action_ingestion_cycle",
        "feature_pipeline_cycle",
        "experiment_pipeline_cycle",
    ]


def test_historical_research_golden_path_experiment_cycle_dispatched_with_plan(
    seeded_historical_research_golden_path_fixture,
    db_session,
):
    fixture = seeded_historical_research_golden_path_fixture

    orchestrator = HistoricalResearchGoldenPathOrchestrator(db_session)

    orchestrator.run(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
        now_utc=fixture.now_utc,
        experiment_plan=fixture.experiment_plan,
    )

    assert len(fixture.fake_orchestration_service.run_experiment_calls) == 1
    assert fixture.fake_orchestration_service.run_experiment_calls[0] is fixture.experiment_plan


# ---------------------------------------------------------------------------
# Without experiment step (backward-compatible default)
# ---------------------------------------------------------------------------


def test_historical_research_golden_path_skips_experiment_when_no_plan(
    seeded_historical_research_golden_path_fixture,
    db_session,
):
    fixture = seeded_historical_research_golden_path_fixture

    orchestrator = HistoricalResearchGoldenPathOrchestrator(db_session)

    result = orchestrator.run(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
        now_utc=fixture.now_utc,
    )

    experiment_jobs = _runtime_job_runs_by_name(db_session, "experiment_pipeline_cycle")
    golden_path_jobs = _runtime_job_runs_by_name(db_session, "historical_research_golden_path")

    assert experiment_jobs == []
    assert fixture.fake_orchestration_service.run_experiment_calls == []

    assert golden_path_jobs[0].status == "completed"
    assert "experiment_pipeline_cycle" not in golden_path_jobs[0].input_summary_json["steps"]

    orchestrator_job = _runtime_job_runs_for_correlation(db_session, result.correlation_id)[0]
    assert orchestrator_job.input_summary_json["steps"] == [
        "market_backfill_cycle",
        "corporate_action_ingestion_cycle",
        "feature_pipeline_cycle",
    ]
