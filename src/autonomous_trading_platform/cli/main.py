import argparse

from autonomous_trading_platform.cli.commands import (
    admin,
    backtesting,
    execution,
    features,
    ingestion,
    research,
    runtime,
    safety,
    strategy,
    universe,
)
from autonomous_trading_platform.cli.runner import run_handler


def build_parser():
    parser = argparse.ArgumentParser(prog="atp")
    subparsers = parser.add_subparsers(dest="domain", required=True)

    safety.register(subparsers)
    ingestion.register(subparsers)
    strategy.register(subparsers)
    execution.register(subparsers)
    runtime.register(subparsers)
    backtesting.register(subparsers)
    admin.register(subparsers)
    universe.register(subparsers)
    features.register(subparsers)
    research.register(subparsers)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return run_handler(args.func, args)


if __name__ == "__main__":
    raise SystemExit(main())
