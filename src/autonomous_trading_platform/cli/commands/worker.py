"""``atp worker``: the research worker's jobs (Box B).

``run`` is the job runner the host timers call: pull datasets → reset state → drop the
stale checkpoint → run a fixture → upload the bundle → write a summary. ``reset`` is the
same state wipe on its own. Both refuse to touch a paper or live database.

``bench-resim`` is the weekly job (plan 3.3a): ask Box A what to re-simulate, pull the
datasets it names, run every strategy over the shared window, and post the outcomes back
as a ``bench_resims`` result for A's portfolio review.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from autonomous_trading_platform.application.services.worker_run_service import (
    WorkerRunRequest,
    WorkerRunService,
)
from autonomous_trading_platform.application.services.worker_state_reset_service import (
    WorkerStateResetService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.contracts.governance.worker_results import (
    WorkerResultStatus,
    WorkerResultType,
)

# Under ./artifacts, which docker-compose.worker.yml mounts, so a payload written inside the
# throw-away job container survives it.
BENCH_RESIMS_DIR = Path("artifacts/worker/bench_resims")


def default_bench_resims_path(run_id: str) -> Path:
    return BENCH_RESIMS_DIR / f"{run_id}.json"


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "worker",
        help="Research worker jobs: run a fixture end to end, reset backtest state.",
    )
    sub = parser.add_subparsers(dest="worker_command", required=True)

    run = sub.add_parser(
        "run",
        help="Pull datasets, reset state, run a platform fixture, upload bundle + summary.",
    )
    run.add_argument("--fixture", required=True, type=Path, help="platform_replay YAML fixture.")
    run.add_argument(
        "--name",
        default=None,
        help="Run name (bundle, checkpoint and S3 folder). Default: the fixture file's stem.",
    )
    run.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Pin the dataset pull to the set Box A held after this session (YYYY-MM-DD).",
    )
    run.add_argument("--no-pull", action="store_true", help="Skip the dataset pull.")
    run.add_argument("--no-reset", action="store_true", help="Keep existing DB state.")
    run.add_argument(
        "--resume",
        action="store_true",
        help="Keep an existing checkpoint and resume from it (implies --no-reset).",
    )
    run.add_argument("--no-upload", action="store_true", help="Leave everything local.")
    run.add_argument("--cadence-minutes", type=int, default=None)
    run.add_argument("--inject-failures", action="store_true")
    run.add_argument("--actor", default="worker")
    run.set_defaults(func=handle_run)

    reset = sub.add_parser(
        "reset", help="Delete all backtest state tables (keeps pulled datasets)."
    )
    reset.add_argument(
        "--drop-pulled-datasets",
        action="store_true",
        help="Also delete the dataset_versions rows registered by `atp datasets pull`.",
    )
    reset.set_defaults(func=handle_reset)

    publish = sub.add_parser(
        "publish-result",
        help="Post a result to Box A (BOX_A_API_URL / BOX_A_API_TOKEN); prints A's decision.",
    )
    publish.add_argument("--run-id", required=True, help="Worker run id (idempotency key).")
    publish.add_argument(
        "--type",
        dest="result_type",
        required=True,
        choices=[t.value for t in WorkerResultType],
    )
    publish.add_argument(
        "--payload", required=True, type=Path, help="JSON file with the typed payload."
    )
    publish.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Session whose publish index the datasets were pinned to.",
    )
    publish.add_argument("--fixture", default=None)
    publish.add_argument(
        "--dataset-version", dest="dataset_versions", action="append", default=None
    )
    publish.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="A worker run summary.json: fills git SHA, host, fixture and as-of when omitted.",
    )
    publish.set_defaults(func=handle_publish_result)

    resim = sub.add_parser(
        "bench-resim",
        help="Weekly: fetch Box A's bench inputs, re-simulate them, post a bench_resims result.",
    )
    resim.add_argument(
        "--run-id",
        default=None,
        help="Worker run id (idempotency key on A). Default: bench_resims_<as-of>.",
    )
    resim.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Window end and dataset pin (YYYY-MM-DD). Default: today (UTC).",
    )
    resim.add_argument("--trading-days", type=int, default=None, help="Override A's window.")
    resim.add_argument("--no-pull", action="store_true", help="Skip the dataset pull.")
    resim.add_argument(
        "--no-publish", action="store_true", help="Write the payload file but do not post it."
    )
    resim.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Where to write the bench_resims payload JSON. "
            f"Default: {BENCH_RESIMS_DIR}/<run-id>.json (the mounted artifacts folder)."
        ),
    )
    resim.set_defaults(func=handle_bench_resim)


# ------------------------------------------------------------------ wiring


def _pull_step(as_of: date | None) -> dict[str, Any]:
    from autonomous_trading_platform.application.services.dataset_pull_service import (
        DatasetPullService,
    )
    from autonomous_trading_platform.cli.commands.datasets import _store
    from autonomous_trading_platform.config.settings import Settings
    from autonomous_trading_platform.db import session_scope

    settings = Settings()
    with session_scope() as session:
        result = DatasetPullService(
            _store(settings), session=session, prefix=settings.dataset_s3_prefix
        ).pull(as_of=as_of)
    return result.summary()


def _reset_step() -> dict[str, Any]:
    from autonomous_trading_platform.db import session_scope

    with session_scope() as session:
        return WorkerStateResetService(session).reset().summary()


def _artifact_store():
    from autonomous_trading_platform.cli.commands.datasets import _store
    from autonomous_trading_platform.config.settings import Settings

    return _store(Settings())


def handle_run(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.platform import handle_backtest_run

    WorkerStateResetService.check_environment()
    name = args.name or args.fixture.stem
    request = WorkerRunRequest(
        name=name,
        fixture=args.fixture,
        as_of=args.as_of,
        pull=not args.no_pull,
        reset=not (args.no_reset or args.resume),
        resume=args.resume,
        upload=not args.no_upload,
        cadence_minutes=args.cadence_minutes,
        inject_failures=args.inject_failures,
        actor=args.actor,
    )
    print_header(f"Worker run {name} ({args.fixture})")
    service = WorkerRunService(
        run_backtest=handle_backtest_run,
        pull_datasets=_pull_step,
        reset_state=_reset_step,
        store=_artifact_store() if request.upload else None,
        git_sha=os.getenv("GIT_SHA") or None,
        host_name=os.getenv("HOST_NAME") or None,
    )
    result = service.run(request)
    print_json(result.summary)
    if result.status == "failed":
        print_error(f"worker run {name} failed; summary at {result.summary_path}")
    return result.exit_code


def handle_reset(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.db import session_scope

    WorkerStateResetService.check_environment()
    with session_scope() as session:
        result = WorkerStateResetService(session).reset(
            keep_pulled_datasets=not args.drop_pulled_datasets
        )
    print_json({**result.summary(), "deleted": {k: v for k, v in result.deleted.items() if v}})
    return 0


def build_submission(args: argparse.Namespace) -> Any:
    """A WorkerResultSubmission from the CLI flags, a run summary filling the gaps."""
    from autonomous_trading_platform.contracts.governance.worker_results import (
        WorkerResultSubmission,
    )

    summary: dict[str, Any] = {}
    if args.summary is not None:
        summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    as_of = args.as_of or (
        date.fromisoformat(summary["datasets_as_of"]) if summary.get("datasets_as_of") else None
    )
    return WorkerResultSubmission(
        run_id=args.run_id,
        result_type=WorkerResultType(args.result_type),
        produced_at=datetime.now(UTC),
        git_sha=summary.get("git_sha") or os.getenv("GIT_SHA") or None,
        worker_host=summary.get("host_name") or os.getenv("HOST_NAME") or None,
        fixture=args.fixture or summary.get("fixture"),
        dataset_as_of=as_of,
        dataset_versions=list(args.dataset_versions or []),
        payload=payload,
    )


def handle_publish_result(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.worker_result_client import (
        WorkerResultClient,
        WorkerResultPublishError,
    )

    submission = build_submission(args)
    print_header(f"Publishing {submission.result_type.value} result {submission.run_id} to Box A")
    try:
        decision = WorkerResultClient.from_env().publish(submission)
    except WorkerResultPublishError as exc:
        print_error(str(exc))
        if exc.body is not None:
            print_json({"status_code": exc.status_code, "body": exc.body})
        return 1
    print_json(decision.model_dump(mode="json"))
    if decision.status is WorkerResultStatus.REJECTED:
        print_error(f"Box A rejected {submission.run_id}: {decision.reason}")
        return 2
    return 0


# ------------------------------------------------------------------ bench re-sim (3.3a)


def handle_bench_resim(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.worker_result_client import (
        WorkerResultClient,
        WorkerResultPublishError,
    )

    WorkerStateResetService.check_environment()
    as_of = args.as_of or datetime.now(UTC).date()
    run_id = args.run_id or f"bench_resims_{as_of.isoformat()}"
    out_path = args.out or default_bench_resims_path(run_id)
    print_header(f"Bench re-sim {run_id} (as of {as_of})")

    try:
        client = WorkerResultClient.from_env()
        inputs = client.fetch_bench_inputs(as_of=as_of, trading_days=args.trading_days)
    except WorkerResultPublishError as exc:
        print_error(str(exc))
        if exc.body is not None:
            print_json({"status_code": exc.status_code, "body": exc.body})
        return 1

    local_sha = os.getenv("GIT_SHA") or None
    summary: dict[str, Any] = {
        "run_id": run_id,
        "as_of": as_of.isoformat(),
        "strategies": [s.strategy_id for s in inputs.strategies],
        "window_start": inputs.window_start.isoformat() if inputs.window_start else None,
        "window_end": inputs.window_end.isoformat() if inputs.window_end else None,
        "dataset_version": inputs.dataset_version,
        "box_a_git_sha": inputs.git_sha,
        "git_sha": local_sha,
    }
    if not inputs.strategies:
        print_json({**summary, "status": "nothing_to_do", "reason": "no_tracked_strategies"})
        return 0
    if inputs.window_start is None or inputs.window_end is None or not inputs.dataset_version:
        reason = inputs.window_reason or "no_window"
        print_json({**summary, "status": "nothing_to_do", "reason": reason})
        return 0
    if inputs.git_sha and local_sha and not _same_sha(inputs.git_sha, local_sha):
        print_error(
            f"git SHA differs from Box A ({local_sha} vs {inputs.git_sha}); "
            "A rejects the result unless worker_result_require_matching_git_sha is off"
        )

    if not args.no_pull:
        summary["pull"] = _pull_step(as_of)

    payload = _resimulate_inputs(inputs, run_id=run_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, default=str), encoding="utf-8"
    )
    summary["payload_path"] = str(out_path)
    summary["outcomes"] = {
        o.strategy_id: {"score": str(o.score) if o.score is not None else None, "error": o.error}
        for o in payload.outcomes
    }

    if args.no_publish:
        print_json({**summary, "status": "not_published"})
        return 0

    from autonomous_trading_platform.contracts.governance.worker_results import (
        WorkerResultSubmission,
    )

    submission = WorkerResultSubmission(
        run_id=run_id,
        result_type=WorkerResultType.BENCH_RESIMS,
        produced_at=datetime.now(UTC),
        git_sha=local_sha,
        worker_host=os.getenv("HOST_NAME") or None,
        dataset_as_of=as_of,
        dataset_versions=[inputs.dataset_version],
        payload=payload.model_dump(mode="json"),
    )
    try:
        decision = client.publish(submission)
    except WorkerResultPublishError as exc:
        print_error(str(exc))
        print_json({**summary, "status": "publish_failed", "body": exc.body})
        return 1
    print_json(
        {**summary, "status": decision.status.value, "decision": decision.model_dump(mode="json")}
    )
    if decision.status is WorkerResultStatus.REJECTED:
        print_error(f"Box A rejected {run_id}: {decision.reason}")
        return 2
    return 0


def _same_sha(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return a.startswith(b) or b.startswith(a)


def _resimulate_inputs(inputs: Any, *, run_id: str) -> Any:
    """Run every strategy A listed over A's window on this box's pulled datasets."""
    from autonomous_trading_platform.application.services.bench_resimulation_service import (
        BenchResimSpec,
        BenchResimulationService,
        BenchWindow,
    )
    from autonomous_trading_platform.application.services.platform_replay.bench_hooks import (
        market_daily_returns,
    )
    from autonomous_trading_platform.application.services.worker_result_apply_service import (
        outcomes_to_payload,
    )
    from autonomous_trading_platform.contracts.common.enums import PriceBasis
    from autonomous_trading_platform.db import session_scope
    from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
        build_simulation_context,
    )
    from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

    window = BenchWindow(
        dataset_version=inputs.dataset_version,
        price_basis=PriceBasis(inputs.price_basis or PriceBasis.RAW.value),
        symbols=list(inputs.symbols),
        start_date=inputs.window_start,
        end_date=inputs.window_end,
    )
    specs = [
        BenchResimSpec(
            strategy_id=s.strategy_id,
            strategy_type=s.strategy_type or "",
            parameters=dict(s.parameters),
        )
        for s in inputs.strategies
    ]
    with session_scope() as session:
        if session.get(DatasetVersions, window.dataset_version) is None:
            raise SystemExit(
                f"dataset version {window.dataset_version} is not on this box; "
                "run `atp datasets pull` (or drop --no-pull)"
            )
        context = build_simulation_context(session=session, universe_size=len(window.symbols))
        service = BenchResimulationService(
            session,
            context.simulation_runner,
            initial_cash=float(inputs.initial_cash),
            random_seed=int(inputs.random_seed),
        )
        outcomes = service.resimulate_specs(specs, window=window, review_id=run_id)
        market = market_daily_returns(
            session=session, window=window, simulation_runner=context.simulation_runner
        )
        return outcomes_to_payload(
            outcomes,
            window_start=window.start_date,
            window_end=window.end_date,
            dataset_version=window.dataset_version,
            market_returns=market,
        )
