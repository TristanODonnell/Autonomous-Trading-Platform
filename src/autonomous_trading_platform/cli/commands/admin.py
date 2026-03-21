from __future__ import annotations

import argparse
from dataclasses import dataclass

from dotenv import load_dotenv

from autonomous_trading_platform.cli.formatters import (
    print_header,
    print_json,
    print_kv_rows,
)
from autonomous_trading_platform.config.settings import Settings


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


@dataclass
class AdminDependencies:
    settings: Settings


def build_dependencies() -> AdminDependencies:
    load_dotenv()  # ensures .env is loaded for CLI

    settings = Settings()

    return AdminDependencies(
        settings=settings,
    )


def handle_show_config(_args: argparse.Namespace) -> int:
    deps = build_dependencies()

    print_header("Config")

    data = deps.settings.__dict__

    redacted_keys = {
        "database_url",
        "paper_broker_api_key",
        "paper_broker_api_secret",
        "live_broker_api_key",
        "live_broker_api_secret",
    }

    safe_data = {
        key: "<redacted>" if key in redacted_keys else value for key, value in data.items()
    }

    print_json(safe_data)
    return 0


def handle_show_env(_args: argparse.Namespace) -> int:
    deps = build_dependencies()

    print_header("Environment")

    data = {
        "ENV": deps.settings.app_env,
        "LOG_LEVEL": deps.settings.log_level,
        "DATABASE_URL": "<set>" if deps.settings.database_url else "<unset>",
        "ALPACA_API_KEY": "<set>" if deps.settings.paper_broker_api_key else "<unset>",
        "ALPACA_API_SECRET": "<set>" if deps.settings.paper_broker_api_secret else "<unset>",
    }

    print_kv_rows(data)
    return 0


def handle_list_failed_runs(args: argparse.Namespace) -> int:
    # TODO NEED TO IMPLEMENT STILL

    print_header("Failed Runs")
    print_json(
        {
            "status": "not_implemented",
            "limit": args.limit,
        }
    )
    return 0
