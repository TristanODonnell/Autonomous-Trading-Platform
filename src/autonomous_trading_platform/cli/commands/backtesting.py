from __future__ import annotations

import argparse

from autonomous_trading_platform.cli.formatters import print_header, print_json


def register(subparsers) -> None:
    backtesting_parser = subparsers.add_parser(
        "backtesting",
        help="Backtesting cycle operations",
    )
    backtesting_subparsers = backtesting_parser.add_subparsers(
        dest="backtesting_command",
        required=True,
    )

    run_parser = backtesting_subparsers.add_parser(
        "run",
        help="Run one backtest",
    )
    run_parser.add_argument("--timestamp")
    run_parser.set_defaults(func=handle_run)

    inspect_results_parser = backtesting_subparsers.add_parser(
        "inspect-results",
        help="Inspect backtesting results",
    )
    inspect_results_parser.add_argument("--run-id", required=True)
    inspect_results_parser.set_defaults(func=handle_inspect_results)


def handle_run(args: argparse.Namespace) -> int:
    print_header("Backtesting Run")
    print_json({"timestamp": args.timestamp, "status": "not_implemented"})
    return 0


def handle_inspect_results(args: argparse.Namespace) -> int:
    print_header("Backtesting Results")
    print_json({"run_id": args.run_id, "status": "not_implemented"})
    return 0
