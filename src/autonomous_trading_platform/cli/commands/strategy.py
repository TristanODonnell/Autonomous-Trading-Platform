from __future__ import annotations

import argparse
from dataclasses import dataclass

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.scheduler.cycles.run_trading_evaluation_cycle import (
    run_trading_evaluation_cycle,
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

    evaluate_symbol_parser = strategy_subparsers.add_parser(
        "evaluate-symbol",
        help="Evaluate strategy for one symbol",
    )
    evaluate_symbol_parser.add_argument("--symbol", required=True)
    evaluate_symbol_parser.add_argument("--timestamp")
    evaluate_symbol_parser.set_defaults(func=handle_evaluate_symbol)

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
    inspect_readiness_parser.add_argument("--symbol")
    inspect_readiness_parser.set_defaults(func=handle_inspect_readiness)


def handle_evaluate_symbol(args: argparse.Namespace) -> int:
    print_header("Evaluate Symbol")
    print_json(
        {
            "symbol": args.symbol,
            "timestamp": args.timestamp,
            "status": "not_implemented",
        }
    )
    return 0


def handle_evaluate_bar(args: argparse.Namespace) -> int:

    timestamp = parse_datetime(args.timestamp)

    run_trading_evaluation_cycle(
        timestamp=timestamp,
    )
    print_header("Evaluate Symbol")
    print_json(
        {
            "symbol": args.symbol,
            "timestamp": args.timestamp,
            "status": "not_implemented",
        }
    )
    return 0


def handle_inspect_readiness(args: argparse.Namespace) -> int:
    print_header("Inspect Readiness")
    print_json({"symbol": args.symbol, "status": "not_implemented"})
    return 0
