from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from autonomous_trading_platform.application.services.worker_run_service import (
    WorkerRunRequest,
    WorkerRunService,
)
from autonomous_trading_platform.storage.parquet.object_store import InMemoryObjectStore

FIXED_NOW = datetime(2026, 10, 13, 1, 2, 3, tzinfo=UTC)
STAMP = "20261013T010203Z"


def _bundle(errors: int = 0) -> dict:
    return {
        "replay_id": "r-1",
        "run_id": "run-1",
        "fixture_name": "smoke",
        "symbols": ["SPY", "QQQ"],
        "start_date": "2024-01-02",
        "end_date": "2024-01-31",
        "runtime": {"ticks_attempted": 21, "ticks_ok": 21, "ticks_failed": 0, "total_orders": 7},
        "errors": ["boom"] * errors,
        "warnings": ["w"],
    }


def _fake_backtest(*, exit_code: int = 0, write: bool = True, errors: int = 0, raise_exc=None):
    calls: list[argparse.Namespace] = []

    def run(args: argparse.Namespace) -> int:
        calls.append(args)
        if raise_exc is not None:
            raise raise_exc
        if write:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(_bundle(errors)), encoding="utf-8")
        return exit_code

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _service(tmp_path: Path, run, *, store=None, pull=None, reset=None) -> WorkerRunService:
    return WorkerRunService(
        run_backtest=run,
        pull_datasets=pull,
        reset_state=reset,
        store=store,
        prefix="artifacts/worker",
        artifacts_root=tmp_path / "artifacts",
        git_sha="deadbeef",
        host_name="box-b",
        now=lambda: FIXED_NOW,
    )


def test_happy_path_runs_every_step_and_uploads_bundle_and_summary(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    run = _fake_backtest()
    pulled: list[date | None] = []

    def pull(as_of: date | None) -> dict:
        pulled.append(as_of)
        return {"pulled": ["v1"]}

    service = _service(tmp_path, run, store=store, pull=pull, reset=lambda: {"rows_deleted": 42})
    # a stale checkpoint from a previous attempt
    checkpoint = service.checkpoint_path("smoke")
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}", encoding="utf-8")

    result = service.run(
        WorkerRunRequest(name="smoke", fixture=Path("fixtures/x.yaml"), as_of=date(2026, 10, 12))
    )

    assert result.status == "ok"
    assert result.exit_code == 0
    assert pulled == [date(2026, 10, 12)]
    assert not checkpoint.exists()
    # the backtest was asked to write the bundle where the checkpoint derivation expects it
    args = run.calls[0]
    assert args.output == service.bundle_path("smoke")
    assert args.fixture == Path("fixtures/x.yaml")
    assert args.no_progress is True and args.dry_run is False
    assert result.bundle_path is not None and result.bundle_path.exists()

    s = result.summary
    assert s["status"] == "ok"
    assert s["stamp"] == STAMP
    assert s["datasets_as_of"] == "2026-10-12"
    assert s["git_sha"] == "deadbeef" and s["host_name"] == "box-b"
    assert s["steps"]["pull_datasets"] == {
        "status": "ok",
        "seconds": s["steps"]["pull_datasets"]["seconds"],
        "pulled": ["v1"],
    }
    assert s["steps"]["reset_state"]["rows_deleted"] == 42
    assert s["steps"]["checkpoint"]["removed"] is True
    bt = s["steps"]["backtest"]
    assert bt["status"] == "ok" and bt["exit_code"] == 0
    assert bt["ticks_ok"] == 21 and bt["total_orders"] == 7 and bt["symbols"] == 2
    assert bt["errors"] == 0 and bt["warnings"] == 1
    assert s["uploaded_keys"] == [
        f"artifacts/worker/smoke/{STAMP}/smoke.json",
        f"artifacts/worker/smoke/{STAMP}/summary.json",
    ]
    assert set(store.objects) == set(s["uploaded_keys"])
    # the uploaded summary is the final one (it lists itself)
    remote = json.loads(store.objects[f"artifacts/worker/smoke/{STAMP}/summary.json"])
    assert remote["uploaded_keys"] == s["uploaded_keys"]
    local = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert local == s
    assert (
        result.summary_path
        == tmp_path / "artifacts" / "worker" / "runs" / "smoke" / STAMP / "summary.json"
    )


def test_resume_keeps_checkpoint_and_skips_reset(tmp_path: Path) -> None:
    run = _fake_backtest()
    reset_calls: list[int] = []

    def reset() -> dict:
        reset_calls.append(1)
        return {}

    service = _service(tmp_path, run, reset=reset)
    checkpoint = service.checkpoint_path("smoke")
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}", encoding="utf-8")

    result = service.run(
        WorkerRunRequest(
            name="smoke", fixture=Path("f.yaml"), pull=False, reset=False, resume=True, upload=False
        )
    )

    assert result.status == "ok"
    assert checkpoint.exists()
    assert reset_calls == []
    assert result.summary["steps"]["checkpoint"] == {
        "status": "ok",
        "seconds": 0.0,
        "path": str(checkpoint),
        "resumed": True,
        "removed": False,
    }
    assert result.summary["steps"]["pull_datasets"]["status"] == "skipped"
    assert result.summary["steps"]["upload_bundle"]["status"] == "skipped"
    assert result.uploaded_keys == []


def test_failed_pull_skips_everything_after_it_but_still_writes_summary(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    run = _fake_backtest()

    def bad_pull(as_of):
        raise RuntimeError("bucket unreachable")

    service = _service(tmp_path, run, store=store, pull=bad_pull, reset=lambda: {})

    result = service.run(WorkerRunRequest(name="smoke", fixture=Path("f.yaml")))

    assert result.status == "failed"
    assert result.exit_code == 1
    assert run.calls == []
    steps = result.summary["steps"]
    assert steps["pull_datasets"]["status"] == "failed"
    assert "bucket unreachable" in steps["pull_datasets"]["error"]
    assert steps["reset_state"]["status"] == "skipped"
    assert steps["backtest"] == {
        "status": "skipped",
        "seconds": 0.0,
        "reason": "earlier step failed",
    }
    # no bundle, so only the summary was uploaded
    assert list(store.objects) == [f"artifacts/worker/smoke/{STAMP}/summary.json"]


def test_backtest_exit_1_with_bundle_is_completed_with_errors(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    service = _service(tmp_path, _fake_backtest(exit_code=1, errors=3), store=store)

    result = service.run(
        WorkerRunRequest(name="smoke", fixture=Path("f.yaml"), pull=False, reset=False)
    )

    assert result.status == "completed_with_errors"
    assert result.exit_code == 1
    assert result.summary["steps"]["backtest"]["errors"] == 3
    # the bundle still gets uploaded so the errors can be inspected
    assert f"artifacts/worker/smoke/{STAMP}/smoke.json" in store.objects


def test_backtest_crash_is_failed_and_reported(tmp_path: Path) -> None:
    service = _service(tmp_path, _fake_backtest(raise_exc=ValueError("bad fixture")))

    result = service.run(
        WorkerRunRequest(
            name="smoke", fixture=Path("f.yaml"), pull=False, reset=False, upload=False
        )
    )

    assert result.status == "failed"
    assert result.exit_code == 1
    bt = result.summary["steps"]["backtest"]
    assert bt["status"] == "failed"
    assert bt["error"] == "ValueError: bad fixture"
    assert bt["bundle_written"] is False
    assert result.bundle_path is None


def test_backtest_exit_1_without_bundle_is_failed(tmp_path: Path) -> None:
    service = _service(tmp_path, _fake_backtest(exit_code=1, write=False))

    result = service.run(
        WorkerRunRequest(
            name="smoke", fixture=Path("f.yaml"), pull=False, reset=False, upload=False
        )
    )

    assert result.status == "failed"
    assert result.summary["bundle_path"] is None


def test_summary_upload_failure_does_not_change_run_status(tmp_path: Path) -> None:
    class FlakyStore(InMemoryObjectStore):
        def put_file(self, local_path: Path, key: str) -> None:
            if key.endswith("summary.json"):
                raise OSError("s3 down")
            super().put_file(local_path, key)

    store = FlakyStore()
    service = _service(tmp_path, _fake_backtest(), store=store)

    result = service.run(
        WorkerRunRequest(name="smoke", fixture=Path("f.yaml"), pull=False, reset=False)
    )

    assert result.status == "ok"
    assert result.exit_code == 0
    assert list(store.objects) == [f"artifacts/worker/smoke/{STAMP}/smoke.json"]
    assert result.summary["uploaded_keys"] == [f"artifacts/worker/smoke/{STAMP}/smoke.json"]
    assert result.summary["summary_upload_failed"] is True
    assert json.loads(result.summary_path.read_text(encoding="utf-8")) == result.summary
