from __future__ import annotations

import argparse

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_dependencies,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def register(subparsers) -> None:
    runtime_parser = subparsers.add_parser("runtime", help="Runtime cycle operations")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command", required=True)

    run_cycle_parser = runtime_subparsers.add_parser(
        "run-cycle",
        help="Run one trading cycle",
    )
    run_cycle_parser.add_argument("--timestamp")
    run_cycle_parser.set_defaults(func=handle_run_cycle)

    inspect_manifest_parser = runtime_subparsers.add_parser(
        "inspect-manifest",
        help="Inspect a run manifest",
    )
    inspect_manifest_parser.add_argument("--run-id", required=True)
    inspect_manifest_parser.set_defaults(func=handle_inspect_manifest)

    inspect_audit_parser = runtime_subparsers.add_parser(
        "inspect-audit",
        help="Inspect run audit data",
    )
    inspect_audit_parser.add_argument("--run-id", required=True)
    inspect_audit_parser.set_defaults(func=handle_inspect_audit)


def handle_run_cycle(args: argparse.Namespace) -> int:
    print_header("Run Trading Cycle")
    run_trading_cycle()
    print_json({"status": "completed"})
    return 0


def handle_inspect_manifest(args: argparse.Namespace) -> int:
    print_header("Inspect Manifest")
    deps = build_trading_cycle_dependencies()
    session = deps.session
    try:
        with SorUnitOfWork(session) as uow:
            manifest = uow.run_manifests.get_by_run_id(args.run_id)

        print_json(
            {
                "run_id": args.run_id,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_audit(args: argparse.Namespace) -> int:
    print_header("Inspect Audit")
    deps = build_trading_cycle_dependencies()
    session = deps.session
    try:
        with SorUnitOfWork(session) as uow:
            audit_logs = uow.audit_logs.list_by_run_id(args.run_id)

        print_json(
            {
                "run_id": args.run_id,
                "audit_logs": [log.model_dump(mode="json") for log in audit_logs],
            }
        )
        return 0
    finally:
        session.close()
