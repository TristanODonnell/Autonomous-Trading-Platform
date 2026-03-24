from __future__ import annotations

import argparse

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_dependencies,
)
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


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

    run_corporate_actions_parser = ingestion_subparsers.add_parser(
        "run-corporate-actions",
        help="Run corporate actions ingestion",
    )
    run_corporate_actions_parser.set_defaults(func=handle_run_corporate_actions)

    inspect_bar_parser = ingestion_subparsers.add_parser(
        "inspect-bar",
        help="Inspect a stored market bar",
    )
    inspect_bar_parser.add_argument("--symbol", required=True)
    inspect_bar_parser.add_argument("--timestamp", required=True)
    inspect_bar_parser.set_defaults(func=handle_inspect_bar)


def handle_run_corporate_actions(_args: argparse.Namespace) -> int:
    run_corporate_action_ingestion_cycle()
    print_header("Run Corporate Actions")
    print_json(
        {
            "pipeline": "corporate_actions_ingestion",
            "status": "success",
        }
    )
    return 0


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


def handle_inspect_bar(args: argparse.Namespace) -> int:
    print_header("Inspect Bar")
    timestamp = parse_datetime(args.timestamp)

    deps = build_trading_cycle_dependencies()
    session = deps.session
    try:
        with SorUnitOfWork(session) as uow:
            bar = uow.market_bars.get_by_symbol_timestamp(
                symbol=args.symbol,
                timestamp=timestamp,
            )

        if bar is None:
            print_json(
                {
                    "symbol": args.symbol,
                    "timestamp": args.timestamp,
                    "found": False,
                }
            )
            return 1

        print_json(
            {
                "found": True,
                "bar": {
                    "bar_id": str(bar.bar_id),
                    "symbol": bar.symbol,
                    "timestamp": bar.timestamp.isoformat(),
                    "end_timestamp": (
                        bar.end_timestamp.isoformat()
                        if getattr(bar, "end_timestamp", None) is not None
                        else None
                    ),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                    "interval": (
                        bar.interval.value if hasattr(bar.interval, "value") else str(bar.interval)
                    ),
                    "price_basis": (
                        bar.price_basis.value
                        if hasattr(bar.price_basis, "value")
                        else str(bar.price_basis)
                    ),
                },
            }
        )
        return 0
    finally:
        deps.session.close()
