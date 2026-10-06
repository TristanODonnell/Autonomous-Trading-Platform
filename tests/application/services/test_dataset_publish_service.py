from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from autonomous_trading_platform.application.services.dataset_publish_service import (
    DatasetPublishService,
)
from autonomous_trading_platform.storage.parquet.object_store import InMemoryObjectStore

DAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)


def _seed(root: Path) -> None:
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


def test_publishes_every_version_with_manifest_and_day_index(tmp_path: Path) -> None:
    _seed(tmp_path)
    store = InMemoryObjectStore()
    service = DatasetPublishService(store, data_root=tmp_path, prefix="datasets", git_sha="abc123")

    result = service.publish_new_versions(trading_date=DAY, now_utc=NOW)

    assert sorted(p.dataset_version_id for p in result.published) == [
        "raw_bars_v1",
        "raw_bars_v2",
        "returns_v1",
    ]
    assert result.already_published == []
    # files keep their layout relative to the data root
    assert store.exists(
        "datasets/bars/raw/dataset_version=raw_bars_v1/symbol=AAPL/year=2026/data.parquet"
    )
    assert store.exists("datasets/features/returns/dataset_version=returns_v1/data.parquet")
    manifest = json.loads(store.objects["datasets/manifests/raw_bars_v1.json"])
    assert manifest["dataset_version_id"] == "raw_bars_v1"
    assert manifest["root"] == "datasets/bars/raw/dataset_version=raw_bars_v1"
    assert manifest["git_sha"] == "abc123"
    assert len(manifest["files"]) == 2
    assert all(len(f["sha256"]) == 64 and f["size"] > 0 for f in manifest["files"])
    assert manifest["dataset_version"] is None  # no registry session in this test
    index = json.loads(store.objects["datasets/published/2026-10-05.json"])
    assert sorted(index["published"]) == ["raw_bars_v1", "raw_bars_v2", "returns_v1"]
    assert result.summary()["files_uploaded"] == 4


def test_second_run_uploads_nothing_but_still_writes_the_day_index(tmp_path: Path) -> None:
    _seed(tmp_path)
    store = InMemoryObjectStore()
    service = DatasetPublishService(store, data_root=tmp_path)
    service.publish_new_versions(trading_date=DAY, now_utc=NOW)
    uploads_before = len(store.objects)

    again = service.publish_new_versions(trading_date=date(2026, 10, 6), now_utc=NOW)

    assert again.published == []
    assert sorted(again.already_published) == ["raw_bars_v1", "raw_bars_v2", "returns_v1"]
    assert len(store.objects) == uploads_before + 1  # only the new day's index
    assert json.loads(store.objects["datasets/published/2026-10-06.json"])["published"] == []


def test_interrupted_upload_is_completed_next_run(tmp_path: Path) -> None:
    """Files without a manifest do not count as published: the next run finishes them."""
    _seed(tmp_path)
    store = InMemoryObjectStore()
    service = DatasetPublishService(store, data_root=tmp_path)
    # a previous run died after uploading one file of raw_bars_v2 and before its manifest
    store.objects[
        "datasets/bars/raw/dataset_version=raw_bars_v2/symbol=AAPL/year=2026/data.parquet"
    ] = b"partial"

    result = service.publish_new_versions(trading_date=DAY, now_utc=NOW)

    assert "raw_bars_v2" in [p.dataset_version_id for p in result.published]
    assert (
        store.objects[
            "datasets/bars/raw/dataset_version=raw_bars_v2/symbol=AAPL/year=2026/data.parquet"
        ]
        == b"raw_bars_v2:AAPL"
    )
    assert store.exists("datasets/manifests/raw_bars_v2.json")


def test_new_version_added_later_is_picked_up(tmp_path: Path) -> None:
    _seed(tmp_path)
    store = InMemoryObjectStore()
    service = DatasetPublishService(store, data_root=tmp_path)
    service.publish_new_versions(trading_date=DAY, now_utc=NOW)

    p = tmp_path / "bars" / "raw" / "dataset_version=raw_bars_v3" / "symbol=AAPL"
    p.mkdir(parents=True)
    (p / "data.parquet").write_bytes(b"v3")
    result = service.publish_new_versions(trading_date=date(2026, 10, 6), now_utc=NOW)

    assert [p.dataset_version_id for p in result.published] == ["raw_bars_v3"]


def test_missing_data_root_publishes_nothing(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    result = DatasetPublishService(store, data_root=tmp_path / "nope").publish_new_versions(
        trading_date=DAY, now_utc=NOW
    )
    assert result.published == [] and result.already_published == []
    assert result.index_key == "datasets/published/2026-10-05.json"


def test_s3_store_exists_maps_404_to_false_and_raises_other_errors() -> None:
    from botocore.exceptions import ClientError

    from autonomous_trading_platform.storage.parquet.object_store import S3ObjectStore

    class _Client:
        def __init__(self, code: str | None) -> None:
            self.code = code
            self.calls: list[tuple[str, str]] = []

        def head_object(self, *, Bucket: str, Key: str) -> dict[str, str]:
            self.calls.append((Bucket, Key))
            if self.code is not None:
                raise ClientError({"Error": {"Code": self.code}}, "HeadObject")
            return {}

    assert S3ObjectStore("b", client=_Client(None)).exists("k") is True
    assert S3ObjectStore("b", client=_Client("404")).exists("k") is False
    try:
        S3ObjectStore("b", client=_Client("AccessDenied")).exists("k")
    except ClientError as exc:
        assert exc.response["Error"]["Code"] == "AccessDenied"
    else:
        raise AssertionError("AccessDenied must propagate")


def test_publish_step_uses_the_configured_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_session: object
) -> None:
    from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import ChainContext
    from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
        PaperTradingGoldenPathOrchestrator,
    )

    _seed(tmp_path)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("DATASET_S3_BUCKET", "example-bucket")
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    store = InMemoryObjectStore()
    orchestrator = PaperTradingGoldenPathOrchestrator(
        db_session,  # type: ignore[arg-type]
        object_store_factory=lambda settings: store,
    )
    ctx = ChainContext(
        chain_name="c",
        trading_date=DAY,
        now_utc=NOW,
        session=db_session,
        parent_job_run_id="p",  # type: ignore[arg-type]
    )
    publish_step = next(s for s in orchestrator.eod_chain_steps() if s.name == "publish_datasets")

    assert publish_step.applies is not None and publish_step.applies(ctx) is True
    summary = publish_step.run(ctx)

    assert summary is not None and sorted(summary["published"]) == [
        "raw_bars_v1",
        "raw_bars_v2",
        "returns_v1",
    ]
    assert json.loads(store.objects["datasets/manifests/raw_bars_v1.json"])["git_sha"] == "deadbeef"

    monkeypatch.delenv("DATASET_S3_BUCKET")
    assert publish_step.applies(ctx) is False
