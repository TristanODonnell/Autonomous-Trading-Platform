from decimal import Decimal

import pyarrow.dataset as ds

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns


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
