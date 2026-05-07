import pyarrow as pa
import pyarrow.dataset as ds

import autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def _latest_runtime_job_run(db_session):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "feature_pipeline_cycle")
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )


def _latest_manifest(db_session):
    return db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()


def _feature_dataset_versions(db_session):
    return db_session.query(FeatureDatasetVersions).all()


def _feature_dataset_versions_for_source(db_session, source_dataset_version):
    return (
        db_session.query(FeatureDatasetVersions)
        .filter(FeatureDatasetVersions.source_dataset_version == source_dataset_version)
        .all()
    )


_FEATURE_PARTITIONING = ds.partitioning(
    pa.schema(
        [
            pa.field("symbol", pa.string()),
            pa.field("year", pa.string()),
            pa.field("month", pa.string()),
        ]
    )
)


def _read_feature_rows(data_root):
    features_root = data_root / "features"
    if not features_root.exists():
        return []

    rows = []
    for feature_dir in features_root.iterdir():
        if not feature_dir.is_dir():
            continue
        for version_dir in feature_dir.glob("dataset_version=*"):
            table = ds.dataset(
                str(version_dir), format="parquet", partitioning=_FEATURE_PARTITIONING
            ).to_table()
            rows.extend(table.to_pylist())

    return rows


def test_run_feature_pipeline_cycle_directly_without_scheduler(
    seeded_feature_pipeline_cycle_fixture,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
    )

    assert True


def test_feature_pipeline_cycle_runs_successfully(
    seeded_feature_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
    )

    manifest = _latest_manifest(db_session)

    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert manifest.strategy_id == "feature_pipeline"
    assert manifest.dataset_version == fixture.dataset_version
    assert manifest.price_basis == PriceBasis.RAW
    assert manifest.interval == BarInterval.ONE_DAY


def test_feature_dataset_versions_are_created(
    seeded_feature_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
    )

    feature_versions = _feature_dataset_versions_for_source(
        db_session,
        fixture.dataset_version,
    )

    assert feature_versions != []

    feature_names = {row.feature_name for row in feature_versions}

    assert "returns" in feature_names
    assert "volatility" in feature_names
    assert "moving_average" in feature_names
    assert "liquidity" in feature_names
    assert "regime" in feature_names

    for row in feature_versions:
        assert row.source_dataset_version == fixture.dataset_version
        assert row.underlying_price_basis == PriceBasis.RAW
        assert row.dataset_version_id is not None
        assert row.dataset_name is not None
        assert row.schema_version is not None
        assert row.validation_status in {"validated", "completed", "valid"}
        assert row.computation_parameters is not None
        assert row.storage_path is not None


def test_feature_parquet_outputs_are_written(
    seeded_feature_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
    )

    feature_versions = _feature_dataset_versions_for_source(
        db_session,
        fixture.dataset_version,
    )
    rows = _read_feature_rows(fixture.data_root)

    assert feature_versions != []
    assert rows != []

    symbols_in_rows = {row["symbol"] for row in rows if "symbol" in row}

    assert symbols_in_rows != set()
    assert symbols_in_rows.issubset(set(fixture.symbols))


def test_returns_feature_pipeline_can_run_by_itself(
    seeded_feature_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
        include_returns=True,
        include_volatility=False,
        include_moving_average=False,
        include_liquidity=False,
        include_regime=False,
    )

    feature_versions = _feature_dataset_versions_for_source(
        db_session,
        fixture.dataset_version,
    )
    rows = _read_feature_rows(fixture.data_root)

    assert feature_versions != []
    assert rows != []
    assert {row.feature_name for row in feature_versions} == {"returns"}

    symbols_in_rows = {row["symbol"] for row in rows if "symbol" in row}

    assert symbols_in_rows == set(fixture.symbols)


def test_include_flags_skip_disabled_feature_jobs(
    seeded_feature_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
        include_returns=False,
        include_volatility=False,
        include_moving_average=True,
        include_liquidity=False,
        include_regime=False,
    )

    feature_versions = _feature_dataset_versions_for_source(
        db_session,
        fixture.dataset_version,
    )

    assert feature_versions != []
    assert {row.feature_name for row in feature_versions} == {"moving_average"}


def test_activity_events_are_emitted_for_feature_pipeline_cycle(
    seeded_feature_pipeline_cycle_fixture,
    monkeypatch,
):
    fixture = seeded_feature_pipeline_cycle_fixture

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

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
    )

    assert recorded_events != []
    assert any(event[0] == "started" for event in recorded_events)
    assert any(event[0] == "completed" for event in recorded_events)
    assert not any(event[0] == "failed" for event in recorded_events)

    completed_event = next(event for event in recorded_events if event[0] == "completed")
    completed_metadata = completed_event[1]["metadata"]

    assert completed_metadata["price_basis"] == PriceBasis.RAW.value
    assert completed_metadata["dataset_version_id"] == fixture.dataset_version
    assert completed_metadata["symbols"] == fixture.symbols
    assert completed_metadata["include_returns"] is True
    assert completed_metadata["include_volatility"] is True
    assert completed_metadata["include_moving_average"] is True
    assert completed_metadata["include_liquidity"] is True
    assert completed_metadata["include_regime"] is True


def test_runtime_job_run_is_recorded_for_feature_pipeline_cycle(
    seeded_feature_pipeline_cycle_fixture,
    db_session,
):
    fixture = seeded_feature_pipeline_cycle_fixture

    run_feature_pipeline_cycle(
        price_basis=PriceBasis.RAW,
        dataset_version_id=fixture.dataset_version,
        symbols=fixture.symbols,
        start_date=fixture.start_date,
        end_date=fixture.end_date,
    )

    manifest = _latest_manifest(db_session)
    job = _latest_runtime_job_run(db_session)

    assert manifest is not None
    assert job is not None

    assert job.job_name == "feature_pipeline_cycle"
    assert job.parent_job_run_id is None
    assert job.status == "completed"
    assert job.trigger_type == "scheduler"
    assert job.error_message is None
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.duration_ms is not None
    assert job.duration_ms >= 0

    assert job.correlation_id == str(manifest.run_id)

    assert job.input_summary_json is not None
    assert job.input_summary_json["component"] == "scheduler.run_feature_pipeline_cycle"
    assert job.input_summary_json["price_basis"] == PriceBasis.RAW.value
    assert job.input_summary_json["dataset_version_id"] == fixture.dataset_version
    assert job.input_summary_json["symbols"] == fixture.symbols

    assert job.output_summary_json is not None
    assert job.output_summary_json["dataset_version_id"] == fixture.dataset_version
    assert job.output_summary_json["last_successful_step"] == "feature_pipeline_completed"
    assert job.output_summary_json["include_returns"] is True
    assert job.output_summary_json["include_volatility"] is True
    assert job.output_summary_json["include_moving_average"] is True
    assert job.output_summary_json["include_liquidity"] is True
    assert job.output_summary_json["include_regime"] is True
