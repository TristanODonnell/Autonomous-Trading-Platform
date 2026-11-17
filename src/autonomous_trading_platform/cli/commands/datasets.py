"""``atp datasets``: move versioned Parquet datasets between a box and the S3 bucket.

``publish`` is what Box A's end-of-day chain runs; exposed here for a one-time seed from a
dev machine and for re-running after a failure. ``pull`` is the worker's nightly job.
Both read ``DATASET_S3_BUCKET`` / ``DATASET_S3_PREFIX`` / ``DATASET_S3_REGION`` and
authenticate with whatever the AWS SDK finds (instance role on a box, profile on a dev
machine).
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from autonomous_trading_platform.application.services.dataset_publish_service import (
    DatasetPublishService,
)
from autonomous_trading_platform.application.services.dataset_pull_service import (
    DatasetPullService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import session_scope
from autonomous_trading_platform.storage.parquet.object_store import ObjectStore, S3ObjectStore


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "datasets",
        help="Publish Parquet dataset versions to S3 (Box A) or pull them (worker).",
    )
    sub = parser.add_subparsers(dest="datasets_command", required=True)

    publish = sub.add_parser(
        "publish",
        help="Upload every local dataset version not yet in the bucket, then the day's index.",
    )
    publish.add_argument(
        "--trading-date",
        type=date.fromisoformat,
        default=None,
        help="Date for the publish index (default: today, UTC).",
    )
    publish.add_argument("--data-root", type=Path, default=None, help="Override DATA_ROOT.")
    publish.set_defaults(func=handle_publish)

    pull = sub.add_parser(
        "pull",
        help="Download published dataset versions into DATA_ROOT and register them locally.",
    )
    scope = pull.add_mutually_exclusive_group()
    scope.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Pin to the set Box A held after this session's publish (YYYY-MM-DD).",
    )
    scope.add_argument(
        "--version",
        dest="versions",
        action="append",
        default=None,
        help="Pull only this dataset version id (repeatable).",
    )
    pull.add_argument(
        "--no-register",
        action="store_true",
        help="Download files only; do not insert dataset_versions rows.",
    )
    pull.add_argument("--data-root", type=Path, default=None, help="Override DATA_ROOT.")
    pull.set_defaults(func=handle_pull)

    window = sub.add_parser(
        "build-window",
        help=(
            "Merge backfill versions into one checked raw-bars version for a research "
            "window. Refused, with nothing written, when a session is missing or short."
        ),
    )
    window.add_argument("--start", type=date.fromisoformat, required=True, help="First day.")
    window.add_argument("--end", type=date.fromisoformat, required=True, help="Last day.")
    names = window.add_mutually_exclusive_group(required=True)
    names.add_argument("--symbols", help="Comma-separated symbols, e.g. AAPL,MSFT")
    names.add_argument(
        "--symbols-file", type=Path, help="A file of symbols, separated by commas or lines."
    )
    mode = window.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plan", action="store_true", help="List the versions it would merge; read no bars."
    )
    mode.add_argument("--enforce", action="store_true", help="Merge, check and register.")
    window.add_argument("--data-root", type=Path, default=None, help="Override DATA_ROOT.")
    window.set_defaults(func=handle_build_window)

    remote = sub.add_parser("list-remote", help="List dataset versions published to the bucket.")
    remote.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="List the set from this session's publish index instead of every manifest.",
    )
    remote.set_defaults(func=handle_list_remote)


def _store(settings: Settings) -> ObjectStore:
    if not settings.dataset_s3_bucket:
        raise RuntimeError("DATASET_S3_BUCKET is not set")
    return S3ObjectStore(settings.dataset_s3_bucket, region=settings.dataset_s3_region)


def handle_publish(args: argparse.Namespace) -> int:
    settings = Settings()
    trading_date = args.trading_date or datetime.now(UTC).date()
    print_header(f"Publishing dataset versions to s3://{settings.dataset_s3_bucket}")
    with session_scope() as session:
        result = DatasetPublishService(
            _store(settings),
            session=session,
            data_root=args.data_root,
            prefix=settings.dataset_s3_prefix,
            git_sha=settings.git_sha,
        ).publish_new_versions(trading_date=trading_date)
    print_json(result.summary())
    return 0


def handle_pull(args: argparse.Namespace) -> int:
    settings = Settings()
    print_header(f"Pulling dataset versions from s3://{settings.dataset_s3_bucket}")
    with session_scope() as session:
        result = DatasetPullService(
            _store(settings),
            session=session,
            data_root=args.data_root,
            prefix=settings.dataset_s3_prefix,
        ).pull(versions=args.versions, as_of=args.as_of, register=not args.no_register)
    print_json(result.summary())
    return 0


_PRICE_JUMPS_PRINTED = 50


def handle_build_window(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.research_window_dataset_service import (
        ResearchWindowDatasetService,
    )

    text = args.symbols if args.symbols else args.symbols_file.read_text(encoding="utf-8")
    # Commas, spaces and line breaks all separate names.
    symbols = text.replace(",", " ").split()
    print_header(f"Research window dataset {args.start} to {args.end}")
    try:
        with session_scope() as session:
            result = ResearchWindowDatasetService(session, base_path=args.data_root).build_window(
                start=args.start,
                end=args.end,
                symbols=symbols,
                now_utc=datetime.now(UTC),
                plan_only=args.plan,
            )
    except ValueError as exc:
        print_error(f"Window dataset not built: {exc}")
        return 1
    payload = result.model_dump(mode="json")
    # A long list of jumps would bury the rest: print the first ones and the count.
    payload["price_jump_count"] = len(result.price_jumps)
    payload["price_jumps"] = payload["price_jumps"][:_PRICE_JUMPS_PRINTED]
    print_json(payload)
    if result.status == "refused":
        print_error("Window dataset not built: " + "; ".join(result.errors))
        return 1
    return 0


def handle_list_remote(args: argparse.Namespace) -> int:
    settings = Settings()
    service = DatasetPullService(_store(settings), prefix=settings.dataset_s3_prefix)
    versions = service.versions_as_of(args.as_of) if args.as_of else service.remote_versions()
    print_json({"bucket": settings.dataset_s3_bucket, "count": len(versions), "versions": versions})
    return 0
