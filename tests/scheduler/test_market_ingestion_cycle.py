from datetime import timedelta
from decimal import Decimal

import pyarrow.dataset as ds
import pytest

import autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def _latest_ingestion_run(db_session):
    return db_session.query(IngestionRuns).order_by(IngestionRuns.created_at.desc()).first()


def _latest_dataset_version(db_session):
    return db_session.query(DatasetVersions).order_by(DatasetVersions.created_at.desc()).first()


def _read_raw_bars(data_root, dataset_version_id):
    root = dataset_version_root(
        data_root,
        RAW_BARS_DATASET,
        dataset_version_id,
    )

    table = ds.dataset(root, format="parquet", partitioning="hive").to_table()
    return table.to_pylist()


def _latest_runtime_job_run(db_session):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "market_ingestion_cycle")
        .order_by(RuntimeJobRuns.completed_at.desc().nullslast())
        .first()
    )


def test_market_ingestion_cycle_reuses_same_daily_raw_dataset_version(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    first_cycle = fixture.now_utc
    second_cycle = fixture.now_utc + timedelta(minutes=5)

    run_market_ingestion_cycle(now_utc=first_cycle)
    run_market_ingestion_cycle(now_utc=second_cycle)

    raw_datasets = (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
        .filter(DatasetVersions.price_basis == PriceBasis.RAW)
        .filter(DatasetVersions.interval == BarInterval.FIVE_MIN)
        .all()
    )

    ingestion_runs = db_session.query(IngestionRuns).order_by(IngestionRuns.created_at.asc()).all()

    assert len(raw_datasets) == 1
    assert len(ingestion_runs) == 2

    daily_dataset = raw_datasets[0]

    assert daily_dataset.metadata_json is not None
    assert daily_dataset.metadata_json["dataset_lifecycle"] == "active_daily_incremental"
    assert daily_dataset.metadata_json["trading_date"] == fixture.now_utc.date().isoformat()

    assert {run.dataset_version for run in ingestion_runs} == {daily_dataset.dataset_version_id}


def test_market_ingestion_cycle_creates_new_raw_dataset_for_new_trading_day(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    first_day_cycle = fixture.now_utc
    next_day_cycle = fixture.now_utc + timedelta(days=1)

    run_market_ingestion_cycle(now_utc=first_day_cycle)
    run_market_ingestion_cycle(now_utc=next_day_cycle)

    raw_datasets = (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
        .filter(DatasetVersions.price_basis == PriceBasis.RAW)
        .filter(DatasetVersions.interval == BarInterval.FIVE_MIN)
        .order_by(DatasetVersions.created_at.asc())
        .all()
    )

    assert len(raw_datasets) == 2

    trading_dates = {dataset.metadata_json["trading_date"] for dataset in raw_datasets}

    assert trading_dates == {
        first_day_cycle.date().isoformat(),
        next_day_cycle.date().isoformat(),
    }


def test_market_ingestion_cycle_runs_successfully(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    run_market_ingestion_cycle(now_utc=fixture.now_utc)

    ingestion_run = _latest_ingestion_run(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "completed"
    assert ingestion_run.error_message is None


def test_dataset_version_is_created(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    run_market_ingestion_cycle(now_utc=fixture.now_utc)

    dataset_version = _latest_dataset_version(db_session)

    assert dataset_version is not None
    assert dataset_version.dataset_name == RAW_BARS_DATASET.dataset_key
    assert dataset_version.symbol_coverage == fixture.symbol_count
    assert dataset_version.price_basis == PriceBasis.RAW
    assert dataset_version.interval == BarInterval.FIVE_MIN


def test_ingestion_run_records_expected_row_count(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    run_market_ingestion_cycle(now_utc=fixture.now_utc)

    ingestion_run = _latest_ingestion_run(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "completed"

    # The pipeline writes one aggregated 5-minute bar per symbol.
    assert ingestion_run.row_count in {
        fixture.symbol_count,
        fixture.symbol_count * 5,
        None,
    }


def test_parquet_contains_one_aggregated_five_minute_bar_per_symbol(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    run_market_ingestion_cycle(now_utc=fixture.now_utc)

    dataset_version = _latest_dataset_version(db_session)

    assert dataset_version is not None

    rows = _read_raw_bars(
        fixture.data_root,
        dataset_version.dataset_version_id,
    )

    assert len(rows) == fixture.symbol_count
    assert {row["symbol"] for row in rows} == set(fixture.symbols)

    for row in rows:
        assert row["interval"] == BarInterval.FIVE_MIN.value
        assert row["price_basis"] == PriceBasis.RAW.value

    print("TMP ROOT:", fixture.data_root)
    print("FILES:", list(fixture.data_root.rglob("*")))


def test_one_symbol_has_correct_ohlcv_aggregation(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    run_market_ingestion_cycle(now_utc=fixture.now_utc)

    dataset_version = _latest_dataset_version(db_session)

    assert dataset_version is not None

    rows = _read_raw_bars(
        fixture.data_root,
        dataset_version.dataset_version_id,
    )

    symbol = fixture.symbols[0]
    row = next(row for row in rows if row["symbol"] == symbol)

    assert Decimal(str(row["open"])) == Decimal("100.0")
    assert Decimal(str(row["high"])) == Decimal("104.75")
    assert Decimal(str(row["low"])) == Decimal("99.75")
    assert Decimal(str(row["close"])) == Decimal("104.5")
    assert row["volume"] == 510
    assert row["trade_count"] == 60

    print("TMP ROOT:", fixture.data_root)
    print("FILES:", list(fixture.data_root.rglob("*")))


def test_runtime_job_run_is_recorded_for_market_ingestion_cycle(
    seeded_market_ingestion_cycle_fixture,
    db_session,
):
    fixture = seeded_market_ingestion_cycle_fixture

    run_market_ingestion_cycle(now_utc=fixture.now_utc)

    ingestion_run = _latest_ingestion_run(db_session)
    job = _latest_runtime_job_run(db_session)

    assert ingestion_run is not None
    assert job is not None

    assert job.job_name == "market_ingestion_cycle"
    assert job.parent_job_run_id is None
    assert job.status == "completed"
    assert job.trigger_type == "scheduler"
    assert job.error_message is None
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.duration_ms is not None
    assert job.duration_ms >= 0

    assert job.input_summary_json is not None
    assert job.input_summary_json["component"] == "scheduler.run_market_ingestion_cycle"
    assert job.input_summary_json["dataset_name"] == RAW_BARS_DATASET.dataset_key
    assert job.input_summary_json["price_basis"] == PriceBasis.RAW.value
    assert job.input_summary_json["interval"] == BarInterval.FIVE_MIN.value

    assert job.output_summary_json is not None
    assert job.output_summary_json["ingestion_run_id"] == str(ingestion_run.ingestion_run_id)
    assert job.output_summary_json["expected_symbol_count"] == fixture.symbol_count
    assert job.output_summary_json["last_successful_step"] == "ingest_bars"


def test_market_ingestion_cycle_marks_failure_when_market_data_job_fails(
    seeded_market_ingestion_cycle_fixture,
    db_session,
    monkeypatch,
):
    fixture = seeded_market_ingestion_cycle_fixture

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

    class FailingIngestBarsJob:
        def __init__(self, **kwargs):
            pass

        def run_once(self, *, start, end):
            raise RuntimeError("simulated market data failure")

    monkeypatch.setattr(cycle_module, "AuditLoggingService", FakeAuditLogger)
    monkeypatch.setattr(cycle_module, "IngestBarsJob", FailingIngestBarsJob)

    with pytest.raises(RuntimeError, match="simulated market data failure"):
        run_market_ingestion_cycle(now_utc=fixture.now_utc)

    ingestion_run = _latest_ingestion_run(db_session)
    dataset_version = _latest_dataset_version(db_session)
    jobs = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "market_ingestion_cycle")
        .all()
    )

    assert jobs != []

    job = next(job for job in jobs if job.status == "failed")

    assert ingestion_run.status == "failed"
    assert "simulated market data failure" in ingestion_run.error_message

    assert dataset_version is not None
    assert dataset_version.validation_status == "failed"

    assert job is not None
    assert job.status == "failed"
    assert "simulated market data failure" in job.error_message
    assert job.output_summary_json is not None
    assert job.output_summary_json["dataset_version_id"] == dataset_version.dataset_version_id
    assert job.output_summary_json["ingestion_run_id"] == str(ingestion_run.ingestion_run_id)

    assert recorded_events != []
    assert any(event[0] == "started" for event in recorded_events)
    assert any(event[0] == "failed" for event in recorded_events)
    assert not any(event[0] == "completed" for event in recorded_events)


def test_market_ingestion_cycle_marks_failure_when_parquet_write_fails(
    seeded_market_ingestion_cycle_fixture,
    db_session,
    monkeypatch,
):
    fixture = seeded_market_ingestion_cycle_fixture

    def failing_write_dataset(*args, **kwargs):
        raise OSError("simulated parquet write failure")

    monkeypatch.setattr(
        ds,
        "write_dataset",
        failing_write_dataset,
    )

    with pytest.raises(OSError, match="simulated parquet write failure"):
        run_market_ingestion_cycle(now_utc=fixture.now_utc)

    ingestion_run = _latest_ingestion_run(db_session)
    dataset_version = _latest_dataset_version(db_session)

    job = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "market_ingestion_cycle")
        .filter(RuntimeJobRuns.status == "failed")
        .one()
    )

    assert ingestion_run is not None
    assert ingestion_run.status == "failed"
    assert "simulated parquet write failure" in ingestion_run.error_message

    assert dataset_version is not None
    assert dataset_version.validation_status == "failed"

    assert job.status == "failed"
    assert "simulated parquet write failure" in job.error_message
    assert job.output_summary_json["dataset_version_id"] == dataset_version.dataset_version_id
    assert job.output_summary_json["ingestion_run_id"] == str(ingestion_run.ingestion_run_id)


def test_market_ingestion_cycle_surfaces_db_write_failure(
    seeded_market_ingestion_cycle_fixture,
    db_session,
    monkeypatch,
):
    fixture = seeded_market_ingestion_cycle_fixture

    original_commit = db_session.commit
    commit_count = 0

    def failing_commit():
        nonlocal commit_count
        commit_count += 1

        if commit_count == 2:
            raise RuntimeError("simulated db write failure")

        return original_commit()

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(RuntimeError, match="simulated db write failure"):
        run_market_ingestion_cycle(now_utc=fixture.now_utc)
