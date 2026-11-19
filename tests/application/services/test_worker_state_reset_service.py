from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.worker_state_reset_service import (
    BACKTEST_STATE_TABLES,
    BUILT_RESEARCH_LIFECYCLES,
    ResetRefusedError,
    WorkerStateResetService,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    RESEARCH_WINDOW_LIFECYCLE,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions


def _version(
    version_id: str, *, pulled: bool, metadata: dict[str, Any] | None = None
) -> DatasetVersions:
    metadata_json: dict[str, Any] = {"pulled_from": {"manifest_key": "m"}} if pulled else {"x": 1}
    metadata_json.update(metadata or {})
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
        metadata_json=metadata_json,
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
    assert result.kept_built_dataset_versions == 0
    assert result.summary()["kept_pulled_dataset_versions"] == 1
    assert result.summary()["kept_built_dataset_versions"] == 0
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


# ---------------------------------------------------------------------------
# Research datasets built on the worker (the user's decision 2026-11-19): a reset
# keeps them and the versions they were merged from. They cannot be pulled again.
# ---------------------------------------------------------------------------


def _window(version_id: str, *, sources: list[str]) -> DatasetVersions:
    return _version(
        version_id,
        pulled=False,
        metadata={"dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE, "source_versions": sources},
    )


def _ids(session: Session) -> set[str]:
    return {row.dataset_version_id for row in session.query(DatasetVersions)}


def test_the_research_window_is_a_built_lifecycle() -> None:
    assert RESEARCH_WINDOW_LIFECYCLE in BUILT_RESEARCH_LIFECYCLES


def test_reset_keeps_a_built_research_window_and_the_versions_it_was_merged_from(
    db_session: Session,
) -> None:
    db_session.add_all(
        [
            _window("window_v1", sources=["backfill_a", "backfill_b"]),
            _version("backfill_a", pulled=False, metadata={"dataset_type": "historical_backfill"}),
            _version("backfill_b", pulled=False, metadata={"dataset_type": "historical_backfill"}),
            # A backfill no kept window names: run state, as before.
            _version(
                "backfill_of_a_run", pulled=False, metadata={"dataset_type": "historical_backfill"}
            ),
            _version("pulled_v1", pulled=True),
            _version("local_v1", pulled=False),
        ]
    )
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(app_env="backtest")

    assert _ids(db_session) == {"window_v1", "backfill_a", "backfill_b", "pulled_v1"}
    assert result.kept_built_dataset_versions == 3
    assert result.kept_pulled_dataset_versions == 1
    assert result.deleted["dataset_versions"] == 2
    assert result.summary()["kept_built_dataset_versions"] == 3


def test_dropping_pulled_datasets_leaves_built_research_data(db_session: Session) -> None:
    db_session.add_all(
        [
            _window("window_v1", sources=["backfill_built", "backfill_pulled"]),
            _version("backfill_built", pulled=False),
            # Pulled, and a source of the kept window: it stays, counted as built.
            _version("backfill_pulled", pulled=True),
            _version("pulled_v1", pulled=True),
        ]
    )
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(keep_pulled_datasets=False, app_env="test")

    assert _ids(db_session) == {"window_v1", "backfill_built", "backfill_pulled"}
    assert result.kept_built_dataset_versions == 3
    assert result.kept_pulled_dataset_versions == 0
    assert result.deleted["dataset_versions"] == 1


def test_a_pulled_source_of_a_kept_window_is_counted_once(db_session: Session) -> None:
    db_session.add_all(
        [
            _window("window_v1", sources=["backfill_pulled"]),
            _version("backfill_pulled", pulled=True),
        ]
    )
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(app_env="backtest")

    assert result.kept_built_dataset_versions == 2
    assert result.kept_pulled_dataset_versions == 0
    assert result.deleted["dataset_versions"] == 0


def test_only_a_built_lifecycle_protects_its_sources(db_session: Session) -> None:
    """A merged dataset a run made (not pulled, not a built lifecycle) is run state, and
    naming sources does not keep them."""
    db_session.add_all(
        [
            _version(
                "rolling_of_a_run",
                pulled=False,
                metadata={
                    "dataset_lifecycle": "rolling_bench_window",
                    "source_versions": ["chunk"],
                },
            ),
            _version("chunk", pulled=False),
        ]
    )
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(app_env="backtest")

    assert _ids(db_session) == set()
    assert result.kept_built_dataset_versions == 0
    assert result.deleted["dataset_versions"] == 2


def test_a_window_naming_a_version_with_no_row_is_still_kept(db_session: Session) -> None:
    db_session.add(_window("window_v1", sources=["gone"]))
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(app_env="backtest")

    assert _ids(db_session) == {"window_v1"}
    assert result.kept_built_dataset_versions == 1


@pytest.mark.parametrize("metadata", [None, [], "text", {"dataset_lifecycle": 7}])
def test_a_row_with_unreadable_metadata_is_run_state(db_session: Session, metadata: Any) -> None:
    row = _version("odd_v1", pulled=False)
    row.metadata_json = metadata
    db_session.add(row)
    db_session.flush()

    result = WorkerStateResetService(db_session).reset(app_env="backtest")

    assert _ids(db_session) == set()
    assert result.deleted["dataset_versions"] == 1
