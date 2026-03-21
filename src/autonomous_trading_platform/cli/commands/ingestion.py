from __future__ import annotations

import argparse
from dataclasses import dataclass

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)


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

    # TODO NEED TO ADD MORE RUNNER CAPABILITY FOR CORPORATE ACTIONS


@dataclass
class IngestionDependencies:
    pass


def build_dependencies():
    pass


def handle_run_bars(args) -> int:
    now_utc = parse_datetime(args.timestamp) if args.timestamp else None

    run_market_ingestion_cycle(now_utc=now_utc)
    print_header("Run Bars")
    print_json({"status": "success"})
    return 0


def handle_run_backfill(args: argparse.Namespace) -> int:
    start = parse_datetime(args.start)
    end = parse_datetime(args.end)

    run_market_backfill_cycle(
        symbols=[args.symbol],
        start=start,
        end=end,
    )

    print_header("Run Backfill")
    print_json(
        {
            "symbol": args.symbol,
            "start": args.start,
            "end": args.end,
            "status": "success",
        }
    )
    return 0

    # TODO NEED TO IMPLEMENT INSPECT BAR HANDLER


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
