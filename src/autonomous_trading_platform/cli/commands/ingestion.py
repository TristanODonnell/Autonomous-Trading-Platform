from __future__ import annotations

import argparse

from autonomous_trading_platform.cli.formatters import print_header, print_json


def register(subparsers) -> None:
    ingestion_parser = subparsers.add_parser("ingestion", help="Ingestion operations")
    ingestion_subparsers = ingestion_parser.add_subparsers(
        dest="ingestion_command",
        required=True,
    )

    run_bars_parser = ingestion_subparsers.add_parser(
        "run-bars",
        help="Run bar ingestion",
    )
    run_bars_parser.add_argument("--timestamp")
    run_bars_parser.set_defaults(func=handle_run_bars)

    run_backfill_parser = ingestion_subparsers.add_parser(
        "run-backfill",
        help="Run historical backfill",
    )
    run_backfill_parser.add_argument("--symbol", required=True)
    run_backfill_parser.add_argument("--start", required=True)
    run_backfill_parser.add_argument("--end", required=True)
    run_backfill_parser.set_defaults(func=handle_run_backfill)

    inspect_bar_parser = ingestion_subparsers.add_parser(
        "inspect-bar",
        help="Inspect a stored market bar",
    )
    inspect_bar_parser.add_argument("--symbol", required=True)
    inspect_bar_parser.add_argument("--timestamp", required=True)
    inspect_bar_parser.set_defaults(func=handle_inspect_bar)


def handle_run_bars(args: argparse.Namespace) -> int:
    print_header("Run Bars")
    print_json({"timestamp": args.timestamp, "status": "not_implemented"})
    return 0


def handle_run_backfill(args: argparse.Namespace) -> int:
    print_header("Run Backfill")
    print_json(
        {
            "symbol": args.symbol,
            "start": args.start,
            "end": args.end,
            "status": "not_implemented",
        }
    )
    return 0


def handle_inspect_bar(args: argparse.Namespace) -> int:
    print_header("Inspect Bar")
    print_json(
        {
            "symbol": args.symbol,
            "timestamp": args.timestamp,
            "status": "not_implemented",
        }
    )
    return 0
