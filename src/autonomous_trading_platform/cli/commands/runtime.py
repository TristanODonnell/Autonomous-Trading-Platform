from __future__ import annotations

import argparse

from autonomous_trading_platform.cli.formatters import print_header, print_json


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
    print_json({"timestamp": args.timestamp, "status": "not_implemented"})
    return 0


def handle_inspect_manifest(args: argparse.Namespace) -> int:
    print_header("Inspect Manifest")
    print_json({"run_id": args.run_id, "status": "not_implemented"})
    return 0


def handle_inspect_audit(args: argparse.Namespace) -> int:
    print_header("Inspect Audit")
    print_json({"run_id": args.run_id, "status": "not_implemented"})
    return 0
