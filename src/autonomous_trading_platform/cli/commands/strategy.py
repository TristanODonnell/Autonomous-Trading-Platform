from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import uuid4

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.scheduler.cycles.run_trading_evaluation_cycle import (
    run_trading_evaluation_cycle,
)
from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
    check_ingestion_readiness_job,
)


@dataclass
class StrategyDependencies:
    pass


def register(subparsers) -> None:
    strategy_parser = subparsers.add_parser("strategy", help="Strategy operations")
    strategy_subparsers = strategy_parser.add_subparsers(
        dest="strategy_command",
        required=True,
    )

    evaluate_bar_parser = strategy_subparsers.add_parser(
        "evaluate-bar",
        help="Evaluate strategy for one bar",
    )
    evaluate_bar_parser.add_argument("--timestamp", required=True)
    evaluate_bar_parser.set_defaults(func=handle_evaluate_bar)

    inspect_readiness_parser = strategy_subparsers.add_parser(
        "inspect-readiness",
        help="Inspect strategy readiness",
    )
    inspect_readiness_parser.add_argument("--timestamp")
    inspect_readiness_parser.set_defaults(func=handle_inspect_readiness)


def handle_evaluate_bar(args: argparse.Namespace) -> int:

    timestamp = parse_datetime(args.timestamp)

    run_trading_evaluation_cycle(
        timestamp=timestamp,
    )
    print_header("Evaluate Bar")
    print_json(
        {
            "timestamp": args.timestamp,
            "status": "success",
        }
    )
    return 0


def handle_inspect_readiness(args: argparse.Namespace) -> int:
    timestamp = parse_datetime(args.timestamp) if args.timestamp else None
    result = check_ingestion_readiness_job(
        run_id=str(uuid4()),
        now_utc=timestamp,
    )
    print_header("Inspect Readiness")
    print_json(
        {
            "timestamp": args.timestamp,
            "ready": result.ready,
            "safe_mode": result.safe_mode,
            "reason": result.reason,
        }
    )
    return 0
