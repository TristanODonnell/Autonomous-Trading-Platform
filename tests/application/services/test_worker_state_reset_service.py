from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.worker_state_reset_service import (
    BACKTEST_STATE_TABLES,
    ResetRefusedError,
    WorkerStateResetService,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions


def _version(version_id: str, *, pulled: bool) -> DatasetVersions:
    return DatasetVersions(
        dataset_version_id=version_id,
        dataset_name="raw_bars",
        created_at=datetime(2026, 10, 12, tzinfo=UTC),
        source="alpaca",
        price_basis=PriceBasis.RAW,
        interval=BarInterval.FIVE_MIN,
        schema_version="1.0.0",
        date_coverage_start=date(2026, 10, 12),
        date_coverage_end=date(2026, 10, 12),
        validation_status="validated",
        metadata_json={"pulled_from": {"manifest_key": "m"}} if pulled else {"x": 1},
    )


def test_table_order_keeps_children_before_parents() -> None:
    order = {name: i for i, name in enumerate(BACKTEST_STATE_TABLES)}
    assert order["symbol_date_coverages"] < order["dataset_versions"]
    assert order["missing_bar_incidents"] < order["ingestion_runs"]
    assert order["simulation_runs"] < order["dataset_versions"]
    assert order["runtime_job_run_steps"] < order["runtime_job_runs"]
    assert len(set(BACKTEST_STATE_TABLES)) == len(BACKTEST_STATE_TABLES)


def test_refuses_outside_backtest_dev_test() -> None:
    for env in ("paper", "live", "PAPER", "prod"):
        with pytest.raises(ResetRefusedError):
            WorkerStateResetService.check_environment(env)
    for env in ("backtest", "dev", "test", "Backtest"):
        WorkerStateResetService.check_environment(env)


def test_reset_clears_state_but_keeps_pulled_dataset_versions(db_session: Session) -> None:
    db_session.add_all([_version("pulled_v1", pulled=True), _version("local_v1", pulled=False)])
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(app_env="backtest")

    assert db_session.get(DatasetVersions, "pulled_v1") is not None
    assert db_session.get(DatasetVersions, "local_v1") is None
    assert result.deleted["dataset_versions"] == 1
    assert result.kept_pulled_dataset_versions == 1
    assert result.summary()["kept_pulled_dataset_versions"] == 1
    assert result.rows_deleted >= 1
    # every listed table exists in the test schema, so nothing was skipped
    assert result.skipped_missing == []


def test_reset_can_drop_pulled_dataset_versions_too(db_session: Session) -> None:
    db_session.add(_version("pulled_v1", pulled=True))
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(keep_pulled_datasets=False, app_env="test")

    assert db_session.get(DatasetVersions, "pulled_v1") is None
    assert result.kept_pulled_dataset_versions == 0
    assert result.deleted["dataset_versions"] == 1


def test_reset_refuses_in_paper_env_without_touching_anything(db_session: Session) -> None:
    db_session.add(_version("local_v1", pulled=False))
    db_session.flush()

    with pytest.raises(ResetRefusedError):
        WorkerStateResetService(db_session).reset(app_env="paper")

    assert db_session.get(DatasetVersions, "local_v1") is not None
