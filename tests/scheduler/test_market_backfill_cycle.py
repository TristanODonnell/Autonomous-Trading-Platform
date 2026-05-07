import pyarrow.dataset as ds

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow


def _latest_ingestion_run(db_session):
    return db_session.query(IngestionRuns).order_by(IngestionRuns.created_at.desc()).first()


def _latest_dataset_version(db_session):
    return db_session.query(DatasetVersions).order_by(DatasetVersions.created_at.desc()).first()


def _latest_manifest(db_session):
    return db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()


def _read_raw_bars(data_root, dataset_version_id):
    root = dataset_version_root(
        data_root,
        RAW_BARS_DATASET,
        dataset_version_id,
    )

    table = ds.dataset(root, format="parquet", partitioning="hive").to_table()
    return table.to_pylist()


def test_run_market_backfill_cycle_directly_without_scheduler(
    seeded_market_backfill_cycle_fixture,
):
    fixture = seeded_market_backfill_cycle_fixture

    run_market_backfill_cycle(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
    )

    assert True


def test_market_backfill_cycle_runs_successfully(
    seeded_market_backfill_cycle_fixture,
    db_session,
):
    fixture = seeded_market_backfill_cycle_fixture

    run_market_backfill_cycle(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
    )

    ingestion_run = _latest_ingestion_run(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "completed"
    assert ingestion_run.error_message is None


def test_market_backfill_creates_raw_dataset_version(
    seeded_market_backfill_cycle_fixture,
    db_session,
):
    fixture = seeded_market_backfill_cycle_fixture

    run_market_backfill_cycle(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
    )

    dataset_version = _latest_dataset_version(db_session)

    assert dataset_version is not None
    assert dataset_version.dataset_name == "bars"
    assert dataset_version.symbol_coverage == fixture.symbol_count
    assert dataset_version.price_basis == PriceBasis.RAW
    assert dataset_version.interval == BarInterval.FIVE_MIN
    assert dataset_version.date_coverage_start == fixture.start.date()
    assert dataset_version.date_coverage_end == fixture.end.date()
    assert dataset_version.metadata_json is not None
    assert dataset_version.metadata_json["dataset_type"] == "historical_backfill"


def test_market_backfill_ingestion_run_references_dataset_version(
    seeded_market_backfill_cycle_fixture,
    db_session,
):
    fixture = seeded_market_backfill_cycle_fixture

    run_market_backfill_cycle(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
    )

    ingestion_run = _latest_ingestion_run(db_session)
    dataset_version = _latest_dataset_version(db_session)

    assert ingestion_run is not None
    assert dataset_version is not None

    assert ingestion_run.dataset_version == dataset_version.dataset_version_id
    assert ingestion_run.run_type.value == "backfill"
    assert ingestion_run.source == "alpaca"
    assert ingestion_run.status == "completed"


def test_market_backfill_writes_raw_bar_parquet(
    seeded_market_backfill_cycle_fixture,
    db_session,
):
    fixture = seeded_market_backfill_cycle_fixture

    run_market_backfill_cycle(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
    )

    dataset_version = _latest_dataset_version(db_session)

    assert dataset_version is not None

    rows = _read_raw_bars(
        fixture.data_root,
        dataset_version.dataset_version_id,
    )

    assert rows != []

    symbols_in_rows = {row["symbol"] for row in rows}

    assert symbols_in_rows == set(fixture.symbols)

    for row in rows:
        assert row["symbol"] in fixture.symbols
        assert row["interval"] == BarInterval.FIVE_MIN.value
        assert row["price_basis"] == PriceBasis.RAW.value
        assert row["open"] > 0
        assert row["high"] > 0
        assert row["low"] > 0
        assert row["close"] > 0
        assert row["volume"] > 0


def test_market_backfill_manifest_is_completed(
    seeded_market_backfill_cycle_fixture,
    db_session,
):
    fixture = seeded_market_backfill_cycle_fixture

    run_market_backfill_cycle(
        symbols=fixture.symbols,
        start=fixture.start,
        end=fixture.end,
    )

    manifest = _latest_manifest(db_session)

    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert manifest.price_basis == PriceBasis.RAW
    assert manifest.interval == BarInterval.FIVE_MIN
    assert manifest.start_date == fixture.start.date()
    assert manifest.end_date == fixture.end.date()
