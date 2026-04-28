from __future__ import annotations

import argparse
import json
from datetime import date

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
    build_simulation_context,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
)


def _parse_symbols(raw: str) -> list[str]:
    symbols = [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")
    return symbols


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def register(subparsers) -> None:
    research_parser = subparsers.add_parser("research", help="Research operations")
    research_subparsers = research_parser.add_subparsers(
        dest="research_command",
        required=True,
    )

    run_simulation_parser = research_subparsers.add_parser(
        "run-simulation",
        help="Run a research simulation",
    )
    run_simulation_parser.add_argument("--dataset-version-id", required=True)
    run_simulation_parser.add_argument(
        "--price-basis",
        required=True,
        choices=["raw", "adjusted"],
    )
    run_simulation_parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols, e.g. SPY,AAPL,MSFT",
    )
    run_simulation_parser.add_argument("--start-date", required=True)
    run_simulation_parser.add_argument("--end-date", required=True)
    run_simulation_parser.add_argument(
        "--strategy-type",
        required=True,
        choices=[
            "stub",
            "moving_average_crossover",
            "mean_reversion",
            "momentum",
            "factor_based",
        ],
        help="Strategy type to run",
    )

    run_simulation_parser.add_argument(
        "--strategy-parameters",
        default="{}",
        help='JSON string of strategy parameters, e.g. \'{"short_window": 10, "long_window": 30}\'',
    )
    run_simulation_parser.add_argument("--strategy-id", required=True)
    run_simulation_parser.add_argument("--initial-cash", type=float, default=100_000.0)
    run_simulation_parser.add_argument("--experiment-id")
    run_simulation_parser.add_argument(
        "--strict-data-loading",
        action="store_true",
        help="Fail if any requested symbol has no bars in the requested window",
    )
    run_simulation_parser.set_defaults(func=handle_run_simulation)


def handle_run_simulation(args: argparse.Namespace) -> int:
    session = get_session()

    try:
        simulation_context = build_simulation_context(session=session)

        strategy_parameters = json.loads(args.strategy_parameters)

        if not isinstance(strategy_parameters, dict):
            raise ValueError("--strategy-parameters must be a JSON object")

        request = SimulationRunRequest(
            strategy_id=args.strategy_id,
            strategy_config={
                "type": args.strategy_type,
                "parameters": strategy_parameters,
            },
            dataset_version=args.dataset_version_id,
            price_basis=PriceBasis(args.price_basis),
            symbols=_parse_symbols(args.symbols),
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
            initial_cash=args.initial_cash,
            experiment_id=args.experiment_id,
            strict_data_loading=args.strict_data_loading,
        )

        result = simulation_context.simulation_runner.run(request)

        print_header("Run Simulation")
        print_json(
            {
                "status": result.status,
                "run_id": str(result.run_id),
                "experiment_id": result.experiment_id,
                "strategy_id": result.strategy_id,
                "strategy_type": args.strategy_type,
                "strategy_parameters": strategy_parameters,
                "dataset_version": result.dataset_version,
                "symbols": result.symbols,
                "symbol_count": len(result.symbols),
                "start_date": result.start_date.isoformat(),
                "end_date": result.end_date.isoformat(),
                "trade_count": result.trade_count,
                "equity_points": result.equity_points,
                "per_bar_metric_points": result.per_bar_metric_points,
            }
        )

        return 0

    finally:
        session.close()
