from __future__ import annotations

import argparse
import os

from autonomous_trading_platform.cli.formatters import (
    print_header,
    print_json,
    print_kv_rows,
    print_success,
)


def register(subparsers) -> None:
    admin_parser = subparsers.add_parser("admin", help="Admin cycle operations")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command", required=True)

    inspect_config_parser = admin_subparsers.add_parser(
        "inspect-config",
        help="Inspect config",
    )
    inspect_config_parser.set_defaults(func=handle_show_config)

    inspect_env_parser = admin_subparsers.add_parser(
        "inspect-env",
        help="Inspect environment",
    )
    inspect_env_parser.set_defaults(func=handle_show_env)

    inspect_failed_runs_parser = admin_subparsers.add_parser(
        "inspect-failed-runs",
        help="Inspect failed runs",
    )
    inspect_failed_runs_parser.add_argument("--limit", type=int, default=25)
    inspect_failed_runs_parser.set_defaults(func=handle_list_failed_runs)


def handle_show_config(args: argparse.Namespace) -> int:
    print_header("Config")
    print_success("Config inspection not wired yet")
    return 0


def handle_show_env(args: argparse.Namespace) -> int:
    print_header("Environment")
    keys = [
        "ENV",
        "LOG_LEVEL",
        "DATABASE_URL",
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
    ]
    data = {key: os.getenv(key, "<unset>") for key in keys}
    print_kv_rows(data)
    return 0


def handle_list_failed_runs(args: argparse.Namespace) -> int:
    print_header("Failed Runs")
    print_json(
        {
            "status": "not_implemented",
            "limit": args.limit,
        }
    )
    return 0
