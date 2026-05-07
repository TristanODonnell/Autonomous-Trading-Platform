from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

CORPORATE_ACTION_NOW_UTC = datetime(2025, 2, 14, 21, 0, tzinfo=UTC)
SOURCE_RAW_BARS_DATASET_VERSION = "corporate_action_fixture_raw_bars_v1"


@dataclass(frozen=True)
class SeededCorporateActionIngestionCycleFixture:
    now_utc: datetime
    source_raw_bars_dataset_version: str
    data_root: Path


class FakeIngestCorporateActionsJob:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def ingest_corporate_actions_job(self) -> None:
        return None


def seed_corporate_action_ingestion_cycle_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededCorporateActionIngestionCycleFixture:
    _seed_environment(monkeypatch)
    _patch_runtime_seams(session=session, data_root=data_root, monkeypatch=monkeypatch)
    _seed_database(session=session)

    return SeededCorporateActionIngestionCycleFixture(
        now_utc=CORPORATE_ACTION_NOW_UTC,
        source_raw_bars_dataset_version=SOURCE_RAW_BARS_DATASET_VERSION,
        data_root=data_root,
    )


def _seed_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")


def _patch_runtime_seams(*, session: Session, data_root: Path, monkeypatch) -> None:
    import autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle as cycle_module
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(cycle_module, "get_session", lambda: session)

    fixed_dataset_version_counter = {"count": 0}

    def fake_generate_dataset_version(dataset_name: str) -> str:
        fixed_dataset_version_counter["count"] += 1
        return f"{dataset_name}_corporate_action_fixture_v{fixed_dataset_version_counter['count']}"

    monkeypatch.setattr(
        cycle_module,
        "generate_dataset_version",
        fake_generate_dataset_version,
    )

    monkeypatch.setattr(
        cycle_module,
        "IngestCorporateActionsJob",
        FakeIngestCorporateActionsJob,
    )

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


def _seed_database(*, session: Session) -> None:
    session.merge(
        DatasetVersions(
            dataset_version_id=SOURCE_RAW_BARS_DATASET_VERSION,
            dataset_name=RAW_BARS_DATASET.dataset_key,
            created_at=CORPORATE_ACTION_NOW_UTC,
            source="corporate_action_fixture",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.ONE_DAY,
            schema_version=RAW_BARS_DATASET.schema_version,
            symbol_coverage=2,
            date_coverage_start=CORPORATE_ACTION_NOW_UTC.date(),
            date_coverage_end=CORPORATE_ACTION_NOW_UTC.date(),
            validation_status="validated",
            checksum="corporate_action_fixture_checksum",
            source_manifest={"symbols": ["SPY", "AAPL"]},
            metadata_json={
                "fixture": "corporate_action_ingestion",
            },
        )
    )

    session.flush()
