from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.dataset_publish_service import (
    DatasetPublishService,
)
from autonomous_trading_platform.application.services.dataset_pull_service import (
    DatasetPullError,
    DatasetPullService,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.storage.parquet.object_store import InMemoryObjectStore
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 23, 0, tzinfo=UTC)


def _seed_box_a(root: Path) -> None:
    for version, symbol in (
        ("raw_bars_v1", "AAPL"),
        ("raw_bars_v1", "MSFT"),
        ("raw_bars_v2", "AAPL"),
    ):
        p = root / "bars" / "raw" / f"dataset_version={version}" / f"symbol={symbol}" / "year=2026"
        p.mkdir(parents=True, exist_ok=True)
        (p / "data.parquet").write_bytes(f"{version}:{symbol}".encode())
    f = root / "features" / "returns" / "dataset_version=returns_v1"
    f.mkdir(parents=True)
    (f / "data.parquet").write_bytes(b"returns")


def _published_store(tmp_path: Path, *, session: Session | None = None) -> InMemoryObjectStore:
    """A bucket exactly as Box A's end-of-day publish leaves it."""
    box_a = tmp_path / "box_a"
    _seed_box_a(box_a)
    store = InMemoryObjectStore()
    DatasetPublishService(
        store, session=session, data_root=box_a, prefix="datasets", git_sha="a1b2c3"
    ).publish_new_versions(trading_date=DAY, now_utc=NOW)
    return store


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_pull_recreates_box_a_layout_byte_for_byte(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    worker = tmp_path / "worker"

    result = DatasetPullService(store, data_root=worker, prefix="datasets").pull()

    assert sorted(p.dataset_version_id for p in result.pulled) == [
        "raw_bars_v1",
        "raw_bars_v2",
        "returns_v1",
    ]
    assert result.unchanged == []
    assert _tree(worker) == _tree(tmp_path / "box_a")
    assert result.summary()["files_downloaded"] == 4
    assert result.summary()["bytes_downloaded"] == sum(
        len(v) for v in _tree(tmp_path / "box_a").values()
    )
    assert not list(worker.rglob("*.partial"))


def test_second_pull_downloads_nothing(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    worker = tmp_path / "worker"
    service = DatasetPullService(store, data_root=worker, prefix="datasets")
    service.pull()

    again = service.pull()

    assert again.pulled == []
    assert sorted(again.unchanged) == ["raw_bars_v1", "raw_bars_v2", "returns_v1"]


def test_version_filter_and_as_of_are_exclusive(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    service = DatasetPullService(store, data_root=tmp_path / "worker", prefix="datasets")

    only = service.pull(versions=["returns_v1"])

    assert [p.dataset_version_id for p in only.pulled] == ["returns_v1"]
    assert not (tmp_path / "worker" / "bars").exists()
    with pytest.raises(ValueError):
        service.pull(versions=["returns_v1"], as_of=DAY)


def test_as_of_pins_to_the_day_index(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    # A later session publishes one more version; pinning to DAY must not fetch it.
    later_root = tmp_path / "box_a"
    extra = later_root / "bars" / "raw" / "dataset_version=raw_bars_v3" / "symbol=AAPL"
    extra.mkdir(parents=True)
    (extra / "data.parquet").write_bytes(b"v3")
    DatasetPublishService(store, data_root=later_root, prefix="datasets").publish_new_versions(
        trading_date=date(2026, 10, 13), now_utc=NOW
    )
    service = DatasetPullService(store, data_root=tmp_path / "worker", prefix="datasets")

    assert service.remote_versions() == ["raw_bars_v1", "raw_bars_v2", "raw_bars_v3", "returns_v1"]
    assert service.versions_as_of(DAY) == ["raw_bars_v1", "raw_bars_v2", "returns_v1"]
    pinned = service.pull(as_of=DAY)
    assert "raw_bars_v3" not in [p.dataset_version_id for p in pinned.pulled]
    assert pinned.as_of == DAY
    assert service.versions_as_of(date(2026, 10, 13)) == [
        "raw_bars_v1",
        "raw_bars_v2",
        "raw_bars_v3",
        "returns_v1",
    ]
    with pytest.raises(DatasetPullError, match="no publish index"):
        service.versions_as_of(date(2026, 1, 1))


def test_corrupt_object_is_rejected_and_nothing_is_left_behind(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    key = "datasets/features/returns/dataset_version=returns_v1/data.parquet"
    store.objects[key] = b"rotten!"  # same length as b"returns", different content
    worker = tmp_path / "worker"

    with pytest.raises(DatasetPullError, match="sha256 mismatch"):
        DatasetPullService(store, data_root=worker, prefix="datasets").pull(versions=["returns_v1"])

    assert not [p for p in worker.rglob("*") if p.is_file()]  # no data, no .partial


def test_size_mismatch_and_missing_manifest_are_errors(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    key = "datasets/features/returns/dataset_version=returns_v1/data.parquet"
    store.objects[key] = b"short"
    service = DatasetPullService(store, data_root=tmp_path / "worker", prefix="datasets")

    with pytest.raises(DatasetPullError, match="manifest says"):
        service.pull(versions=["returns_v1"])
    with pytest.raises(DatasetPullError, match="has no manifest"):
        service.pull(versions=["never_published"])


def test_a_file_that_changed_size_locally_is_refetched(tmp_path: Path) -> None:
    store = _published_store(tmp_path)
    worker = tmp_path / "worker"
    service = DatasetPullService(store, data_root=worker, prefix="datasets")
    service.pull()
    local = worker / "features" / "returns" / "dataset_version=returns_v1" / "data.parquet"
    local.write_bytes(b"truncated-but-longer")

    result = service.pull(versions=["returns_v1"])

    assert result.pulled[0].files_downloaded == 1
    assert local.read_bytes() == b"returns"


def test_unsafe_keys_are_refused(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    store.objects["datasets/manifests/evil.json"] = json.dumps(
        {
            "dataset_version_id": "evil",
            "files": [{"key": "datasets/../../etc/passwd", "size": 1, "sha256": "0" * 64}],
        }
    ).encode()
    service = DatasetPullService(store, data_root=tmp_path / "worker", prefix="datasets")

    with pytest.raises(DatasetPullError, match="unsafe"):
        service.pull(versions=["evil"])


def test_registers_versions_in_the_worker_db_from_the_manifest(
    tmp_path: Path, db_session: Session
) -> None:
    # Box A has the row; the worker's DB (same test session here, emptied below) does not.
    db_session.add(
        DatasetVersions(
            dataset_version_id="raw_bars_v1",
            dataset_name="raw_bars",
            created_at=datetime(2026, 10, 12, 20, 5, tzinfo=UTC),
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="1.0.0",
            symbol_coverage=2,
            date_coverage_start=DAY,
            date_coverage_end=DAY,
            validation_status="validated",
            checksum="abc",
            source_dataset_version=None,
            source_manifest={"symbols": ["AAPL", "MSFT"]},
            metadata_json={"dataset_lifecycle": "active_daily_incremental"},
        )
    )
    db_session.flush()
    store = _published_store(tmp_path, session=db_session)
    manifest = json.loads(store.objects["datasets/manifests/raw_bars_v1.json"])
    assert manifest["dataset_version"]["source_manifest"] == {"symbols": ["AAPL", "MSFT"]}
    assert manifest["dataset_version"]["metadata_json"] == {
        "dataset_lifecycle": "active_daily_incremental"
    }
    # Now play the worker: its own DB starts without the row.
    db_session.query(DatasetVersions).delete()
    db_session.flush()

    service = DatasetPullService(
        store, session=db_session, data_root=tmp_path / "worker", prefix="datasets"
    )
    result = service.pull()

    assert result.summary()["registered"] == ["raw_bars_v1"]
    row = db_session.get(DatasetVersions, "raw_bars_v1")
    assert row is not None
    assert row.dataset_name == "raw_bars"
    assert row.price_basis == PriceBasis.RAW
    assert row.interval == BarInterval.FIVE_MIN
    assert row.date_coverage_start == DAY
    assert row.validation_status == "validated"
    assert row.source_manifest == {"symbols": ["AAPL", "MSFT"]}
    assert row.metadata_json["dataset_lifecycle"] == "active_daily_incremental"
    assert row.metadata_json["pulled_from"]["publisher_git_sha"] == "a1b2c3"
    assert row.metadata_json["pulled_from"]["manifest_key"] == "datasets/manifests/raw_bars_v1.json"
    # Versions Box A never registered (returns_v1 here) are pulled but not registered.
    assert db_session.get(DatasetVersions, "returns_v1") is None

    # A second pull neither re-downloads nor re-registers.
    again = service.pull()
    assert again.pulled == []
    assert sorted(again.unchanged) == ["raw_bars_v1", "raw_bars_v2", "returns_v1"]

    # --no-register leaves the DB alone even when the row is missing.
    db_session.query(DatasetVersions).delete()
    db_session.flush()
    skipped = service.pull(register=False)
    assert skipped.summary()["registered"] == []
    assert db_session.get(DatasetVersions, "raw_bars_v1") is None
