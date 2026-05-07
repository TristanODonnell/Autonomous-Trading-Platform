from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import (
    SymbolDateCoverage,
)

FEATURE_PIPELINE_NOW_UTC = datetime(2025, 2, 14, 21, 0, tzinfo=UTC)
FEATURE_PIPELINE_DATASET_VERSION = "feature_pipeline_fixture_raw_v1"

FEATURE_SYMBOLS = ["SPY", "AAPL"]


@dataclass(frozen=True)
class SeededFeaturePipelineCycleFixture:
    now_utc: datetime
    dataset_version: str
    symbols: list[str]
    data_root: Path
    start_date: date
    end_date: date


# ─────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────


def seed_feature_pipeline_cycle_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededFeaturePipelineCycleFixture:

    _seed_environment(monkeypatch)
    _patch_runtime_seams(session=session, monkeypatch=monkeypatch, data_root=data_root)

    _write_raw_bars(data_root=data_root)
    _seed_database(session=session, data_root=data_root)

    return SeededFeaturePipelineCycleFixture(
        now_utc=FEATURE_PIPELINE_NOW_UTC,
        dataset_version=FEATURE_PIPELINE_DATASET_VERSION,
        symbols=FEATURE_SYMBOLS,
        data_root=data_root,
        start_date=(FEATURE_PIPELINE_NOW_UTC - timedelta(days=250)).date(),
        end_date=FEATURE_PIPELINE_NOW_UTC.date(),
    )


# ─────────────────────────────────────────────────────────────
# ENV + PATCHING
# ─────────────────────────────────────────────────────────────


def _seed_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")


def _patch_runtime_seams(*, session: Session, monkeypatch, data_root: Path):

    import autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle as cycle_module
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(cycle_module, "get_session", lambda: session)

    monkeypatch.setattr(
        parquet_paths,
        "get_data_root",
        lambda: data_root,
        raising=False,
    )

    try:
        from autonomous_trading_platform.config.settings import Settings

        monkeypatch.setattr(
            Settings,
            "DATA_ROOT",
            str(data_root),
            raising=False,
        )
    except Exception:
        pass

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("PARQUET_DATA_ROOT", str(data_root))
    monkeypatch.setenv("LOCAL_DATA_ROOT", str(data_root))


# ─────────────────────────────────────────────────────────────
# PARQUET DATA (INPUT)
# ─────────────────────────────────────────────────────────────


def _write_raw_bars(*, data_root: Path):
    root = dataset_version_root(
        data_root,
        RAW_BARS_DATASET,
        FEATURE_PIPELINE_DATASET_VERSION,
    )

    if root.exists():
        return

    bar_count = 250
    start = FEATURE_PIPELINE_NOW_UTC - timedelta(days=bar_count)

    rows = []

    for symbol_index, symbol in enumerate(FEATURE_SYMBOLS):
        for i in range(bar_count):
            ts = start + timedelta(days=i)
            end_ts = ts + timedelta(days=1)

            base = 100 + symbol_index * 10 + i * 0.1
            close = base + 0.5

            rows.append(
                {
                    "bar_id": f"{symbol}-{ts.isoformat()}-{FEATURE_PIPELINE_DATASET_VERSION}",
                    "timestamp": ts,
                    "end_timestamp": end_ts,
                    "interval": BarInterval.ONE_DAY.value,
                    "symbol": symbol,
                    "open": base,
                    "high": base + 1,
                    "low": base - 1,
                    "close": close,
                    "volume": 1000 + i,
                    "vwap": close,
                    "trade_count": 100 + i,
                    "price_basis": PriceBasis.RAW.value,
                    "adjustment_factor": 1.0,
                    "source": "feature_pipeline_fixture",
                    "ingested_at": FEATURE_PIPELINE_NOW_UTC,
                    "quality_flags": [],
                    "date": ts.date(),
                    "year": f"{ts.year:04d}",
                    "month": f"{ts.month:02d}",
                }
            )

    table = pa.table({k: [r[k] for r in rows] for k in rows[0]})

    write_table(
        table=table,
        dataset=RAW_BARS_DATASET,
        base_path=data_root,
        dataset_version=FEATURE_PIPELINE_DATASET_VERSION,
    )


# ─────────────────────────────────────────────────────────────
# DB SEEDING
# ─────────────────────────────────────────────────────────────


def _seed_database(*, session: Session, data_root: Path):

    start_date = (FEATURE_PIPELINE_NOW_UTC - timedelta(days=250)).date()
    end_date = FEATURE_PIPELINE_NOW_UTC.date()

    session.merge(
        DatasetVersions(
            dataset_version_id=FEATURE_PIPELINE_DATASET_VERSION,
            dataset_name=RAW_BARS_DATASET.dataset_key,
            created_at=FEATURE_PIPELINE_NOW_UTC,
            source="feature_pipeline_fixture",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.ONE_DAY,
            schema_version=RAW_BARS_DATASET.schema_version,
            symbol_coverage=len(FEATURE_SYMBOLS),
            date_coverage_start=start_date,
            date_coverage_end=end_date,
            validation_status="validated",
            checksum="fixture_checksum",
            source_manifest={"symbols": FEATURE_SYMBOLS},
            metadata_json={
                "fixture": "feature_pipeline",
                "storage_path": str(
                    dataset_version_root(
                        data_root,
                        RAW_BARS_DATASET,
                        FEATURE_PIPELINE_DATASET_VERSION,
                    )
                ),
            },
        )
    )

    # coverage (important for pipeline guards)
    for symbol in FEATURE_SYMBOLS:
        session.merge(
            SymbolDateCoverage(
                coverage_id=f"{FEATURE_PIPELINE_DATASET_VERSION}:{symbol}:{end_date}",
                symbol=symbol,
                date=end_date,
                dataset_version=FEATURE_PIPELINE_DATASET_VERSION,
                expected_bar_count=1,
                actual_bar_count=1,
                completeness_status="complete",
                gap_summary=None,
                updated_at=FEATURE_PIPELINE_NOW_UTC,
            )
        )

    session.flush()
