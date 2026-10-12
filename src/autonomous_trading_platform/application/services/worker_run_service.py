"""The worker's job runner: one fixture run, end to end, with a durable summary.

    pull datasets (optional, pinned) → reset DB state → drop stale checkpoint
    → run the platform backtest → upload the artifact bundle → write + upload a summary

Each step is recorded with its outcome and duration, so a failed run still leaves a
summary saying how far it got. The summary and the bundle go to
``<prefix>/<name>/<stamp>/`` in the object store and to
``artifacts/worker/runs/<name>/<stamp>/`` locally.

The steps are injectable so the service can be tested without a database, a bucket or a
real backtest; the CLI wires the real ones.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.parquet.object_store import ObjectStore

logger = get_logger(__name__)

SUMMARY_SCHEMA_VERSION = "1.0.0"
BACKTEST_ARTIFACT_DIR = Path("artifacts/platform/backtests")


@dataclass(frozen=True)
class WorkerRunRequest:
    name: str
    fixture: Path
    as_of: date | None = None  # pin the dataset pull to this session's publish index
    pull: bool = True
    reset: bool = True
    resume: bool = False  # keep an existing checkpoint instead of starting clean
    upload: bool = True
    cadence_minutes: int | None = None
    inject_failures: bool = False
    actor: str = "worker"


@dataclass
class StepRecord:
    name: str
    status: str  # ok | failed | skipped
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "seconds": round(self.seconds, 3),
            **self.detail,
        }
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class WorkerRunResult:
    name: str
    stamp: str
    status: str  # ok | completed_with_errors | failed
    exit_code: int
    steps: list[StepRecord]
    bundle_path: Path | None
    summary_path: Path
    uploaded_keys: list[str]
    summary: dict[str, Any]


# Step callables the CLI provides. Kept as plain callables so tests can swap them.
PullFn = Callable[[date | None], dict[str, Any]]
ResetFn = Callable[[], dict[str, Any]]
BacktestFn = Callable[[argparse.Namespace], int]


class WorkerRunService:
    def __init__(
        self,
        *,
        run_backtest: BacktestFn,
        pull_datasets: PullFn | None = None,
        reset_state: ResetFn | None = None,
        store: ObjectStore | None = None,
        prefix: str = "artifacts/worker",
        artifacts_root: Path = Path("artifacts"),
        backtest_dir: Path | None = None,
        git_sha: str | None = None,
        host_name: str | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_backtest = run_backtest
        self._pull = pull_datasets
        self._reset = reset_state
        self._store = store
        self._prefix = prefix.strip("/")
        self._artifacts_root = artifacts_root
        self._backtest_dir = backtest_dir or (artifacts_root / "platform" / "backtests")
        self._git_sha = git_sha
        self._host_name = host_name
        self._now = now or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ public

    def bundle_path(self, name: str) -> Path:
        return self._backtest_dir / f"{name}.json"

    def checkpoint_path(self, name: str) -> Path:
        # Same derivation as `atp platform backtest run`: <output stem>.checkpoint.json
        return self._backtest_dir / f"{name}.checkpoint.json"

    def run(self, request: WorkerRunRequest) -> WorkerRunResult:
        started = self._now()
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        steps: list[StepRecord] = []
        run_dir = self._artifacts_root / "worker" / "runs" / request.name / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        bundle = self.bundle_path(request.name)
        checkpoint = self.checkpoint_path(request.name)

        logger.info(
            "worker_run.started",
            extra={"run_name": request.name, "fixture": str(request.fixture), "stamp": stamp},
        )

        # 1. datasets
        pull = self._pull
        if request.pull and pull is not None:
            steps.append(self._timed("pull_datasets", lambda: pull(request.as_of)))
        else:
            steps.append(StepRecord("pull_datasets", "skipped"))

        # 2. reset
        if request.reset and self._reset is not None and steps[-1].status != "failed":
            steps.append(self._timed("reset_state", self._reset))
        else:
            steps.append(StepRecord("reset_state", "skipped"))

        # 3. checkpoint
        steps.append(self._checkpoint_step(checkpoint, resume=request.resume))

        # 4. backtest (only if nothing above failed)
        exit_code = 1
        if all(s.status != "failed" for s in steps):
            record, exit_code = self._backtest_step(request, bundle)
            steps.append(record)
        else:
            steps.append(
                StepRecord("backtest", "skipped", detail={"reason": "earlier step failed"})
            )

        # 5. upload bundle
        uploaded: list[str] = []
        if request.upload and self._store is not None and bundle.exists():
            steps.append(
                self._timed(
                    "upload_bundle",
                    lambda: {"keys": self._upload(bundle, request.name, stamp, uploaded)},
                )
            )
        else:
            steps.append(StepRecord("upload_bundle", "skipped"))

        finished = self._now()
        status = self._overall_status(steps, exit_code)
        summary = self._build_summary(
            request,
            stamp=stamp,
            started=started,
            finished=finished,
            status=status,
            exit_code=exit_code,
            steps=steps,
            bundle=bundle,
            uploaded=uploaded,
        )
        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

        # 6. upload summary (best effort; a failure here must not mask the run's status)
        if request.upload and self._store is not None:
            key = f"{self._prefix}/{request.name}/{stamp}/summary.json"
            # Written with its own key first, so the copy in the store lists itself.
            summary["uploaded_keys"] = [*uploaded, key]
            summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            try:
                self._store.put_file(summary_path, key)
                uploaded.append(key)
            except Exception:  # noqa: BLE001 - the local summary records the truth instead
                logger.warning("worker_run.summary_upload_failed", exc_info=True)
                summary["uploaded_keys"] = list(uploaded)
                summary["summary_upload_failed"] = True
                summary_path.write_text(
                    json.dumps(summary, indent=2, default=str), encoding="utf-8"
                )

        logger.info(
            "worker_run.finished",
            extra={"run_name": request.name, "stamp": stamp, "status": status, "exit": exit_code},
        )
        return WorkerRunResult(
            name=request.name,
            stamp=stamp,
            status=status,
            exit_code=exit_code if status != "ok" else 0,
            steps=steps,
            bundle_path=bundle if bundle.exists() else None,
            summary_path=summary_path,
            uploaded_keys=uploaded,
            summary=summary,
        )

    # ----------------------------------------------------------------- steps

    def _timed(self, name: str, fn: Callable[[], dict[str, Any]]) -> StepRecord:
        t0 = time.monotonic()
        try:
            detail = fn() or {}
        except Exception as exc:  # noqa: BLE001 - every step failure is recorded, not raised
            logger.exception("worker_run.step_failed", extra={"step": name})
            return StepRecord(
                name, "failed", time.monotonic() - t0, error=f"{type(exc).__name__}: {exc}"
            )
        return StepRecord(name, "ok", time.monotonic() - t0, detail=detail)

    def _checkpoint_step(self, checkpoint: Path, *, resume: bool) -> StepRecord:
        existed = checkpoint.exists()
        if resume:
            return StepRecord(
                "checkpoint",
                "ok",
                detail={"path": str(checkpoint), "resumed": existed, "removed": False},
            )
        if existed:
            checkpoint.unlink()
        return StepRecord(
            "checkpoint",
            "ok",
            detail={"path": str(checkpoint), "resumed": False, "removed": existed},
        )

    def _backtest_step(self, request: WorkerRunRequest, bundle: Path) -> tuple[StepRecord, int]:
        bundle.parent.mkdir(parents=True, exist_ok=True)
        args = argparse.Namespace(
            fixture=request.fixture,
            symbols=None,
            start=None,
            end=None,
            starting_cash=None,
            random_seed=None,
            cadence_minutes=request.cadence_minutes,
            actor=request.actor,
            dry_run=False,
            inject_failures=request.inject_failures,
            output=bundle,
            no_progress=True,
        )
        t0 = time.monotonic()
        try:
            exit_code = int(self._run_backtest(args))
        except Exception as exc:  # noqa: BLE001 - recorded in the summary
            logger.exception("worker_run.backtest_crashed", extra={"run_name": request.name})
            return (
                StepRecord(
                    "backtest",
                    "failed",
                    time.monotonic() - t0,
                    detail={"bundle": str(bundle), "bundle_written": bundle.exists()},
                    error=f"{type(exc).__name__}: {exc}",
                ),
                1,
            )
        detail: dict[str, Any] = {
            "exit_code": exit_code,
            "bundle": str(bundle),
            "bundle_written": bundle.exists(),
        }
        detail.update(_headline(bundle))
        status = "ok" if exit_code == 0 else "failed"
        return StepRecord("backtest", status, time.monotonic() - t0, detail=detail), exit_code

    def _upload(self, bundle: Path, name: str, stamp: str, uploaded: list[str]) -> list[str]:
        assert self._store is not None
        key = f"{self._prefix}/{name}/{stamp}/{bundle.name}"
        self._store.put_file(bundle, key)
        uploaded.append(key)
        return [key]

    # --------------------------------------------------------------- summary

    @staticmethod
    def _overall_status(steps: list[StepRecord], exit_code: int) -> str:
        backtest = next((s for s in steps if s.name == "backtest"), None)
        if backtest is None or backtest.status != "ok":
            # exit 1 with a written bundle and no crash is "completed_with_errors"
            if (
                backtest is not None
                and backtest.error is None
                and backtest.detail.get("bundle_written")
                and exit_code == 1
            ):
                return "completed_with_errors"
            return "failed"
        if any(s.status == "failed" for s in steps):
            return "completed_with_errors"
        return "ok"

    def _build_summary(
        self,
        request: WorkerRunRequest,
        *,
        stamp: str,
        started: datetime,
        finished: datetime,
        status: str,
        exit_code: int,
        steps: list[StepRecord],
        bundle: Path,
        uploaded: list[str],
    ) -> dict[str, Any]:
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "run_name": request.name,
            "stamp": stamp,
            "status": status,
            "exit_code": exit_code,
            "fixture": str(request.fixture),
            "datasets_as_of": request.as_of.isoformat() if request.as_of else None,
            "git_sha": self._git_sha,
            "host_name": self._host_name,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "options": {
                "pull": request.pull,
                "reset": request.reset,
                "resume": request.resume,
                "upload": request.upload,
                "cadence_minutes": request.cadence_minutes,
                "inject_failures": request.inject_failures,
            },
            "steps": {s.name: s.as_dict() for s in steps},
            "bundle_path": str(bundle) if bundle.exists() else None,
            "uploaded_keys": list(uploaded),
        }


def _headline(bundle: Path) -> dict[str, Any]:
    """The few numbers worth having without opening the bundle."""
    if not bundle.exists():
        return {}
    try:
        data = json.loads(bundle.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"headline_error": "bundle unreadable"}
    runtime = data.get("runtime") or {}
    out: dict[str, Any] = {
        "replay_id": data.get("replay_id"),
        "run_id": data.get("run_id"),
        "fixture_name": data.get("fixture_name"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "symbols": len(data.get("symbols") or []),
        "errors": len(data.get("errors") or []),
        "warnings": len(data.get("warnings") or []),
    }
    for key in ("ticks_attempted", "ticks_ok", "ticks_failed", "total_orders", "total_fills"):
        if key in runtime:
            out[key] = runtime[key]
    return out
