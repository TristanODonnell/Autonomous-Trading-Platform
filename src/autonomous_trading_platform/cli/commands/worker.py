"""``atp worker``: the research worker's jobs (Box B).

``run`` is the job runner the host timers call: pull datasets → reset state → drop the
stale checkpoint → run a fixture → upload the bundle → write a summary. ``reset`` is the
same state wipe on its own. Both refuse to touch a paper or live database.
"""

from __future__ import annotations

import argparse
import os
from datetime import date
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
