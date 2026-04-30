from __future__ import annotations

import argparse
import json
from datetime import date

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
    build_simulation_context,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
)
from autonomous_trading_platform.research.strategy_generation.generators.base_generator import (
    BaseStrategyGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.grid_search_generator import (
    GridSearchGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.random_sampling_generator import (
    RandomSamplingGenerator,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
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
            "intentional_loser",
            "random",
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
    run_simulation_parser.add_argument(
        "--random-seed",
        type=int,
        required=True,
        help="Fixed random seed used for deterministic simulation replay.",
    )
    run_simulation_parser.add_argument(
        "--shuffle-timestamps",
        action="store_true",
        help="Shuffle timestamps to break temporal structure (diagnostic test)",
    )
    run_simulation_parser.add_argument("--strategy-id", required=True)
    run_simulation_parser.add_argument("--initial-cash", type=float, default=100_000.0)
    run_simulation_parser.add_argument("--experiment-id")
    run_simulation_parser.add_argument(
        "--universe-version",
        default="v1",
        help="Universe version tag (used when routing through the experiment orchestrator)",
    )
    run_simulation_parser.add_argument(
        "--strict-data-loading",
        action="store_true",
        help="Fail if any requested symbol has no bars in the requested window",
    )
    run_simulation_parser.set_defaults(func=handle_run_simulation)

    # Independent strategy generation
    register_generate_strategies(research_subparsers)


def handle_run_simulation(args: argparse.Namespace) -> int:
    session = get_session()

    try:
        simulation_context = build_simulation_context(session=session)

        strategy_parameters = json.loads(args.strategy_parameters)

        if not isinstance(strategy_parameters, dict):
            raise ValueError("--strategy-parameters must be a JSON object")

        # --- Route through ExperimentOrchestrationService when an experiment-id
        #     is supplied; fall back to a direct SimulationRunner call for ad-hoc
        #     / debug runs so the existing debug config keeps working unchanged.
        if args.experiment_id:
            plan = ExperimentDefinition(
                experiment_id=args.experiment_id,
                description=None,
                strategy_set=[
                    {
                        "strategy_id": args.strategy_id,
                        "type": args.strategy_type,
                        "parameters": strategy_parameters,
                    }
                ],
                parameter_grid=[],
                dataset_version=args.dataset_version_id,
                universe_version=args.universe_version,
                price_basis=PriceBasis(args.price_basis),
                symbols=_parse_symbols(args.symbols),
                start_date=_parse_date(args.start_date),
                end_date=_parse_date(args.end_date),
                random_seed=args.random_seed,
                initial_cash=args.initial_cash,
                experiment_type=ExperimentType.AB,
            )

            results = simulation_context.experiment_orchestration_service.run_experiment(plan)
            result = results[0]  # single-strategy run always yields one result

        else:
            # Ad-hoc / debug path — bypasses orchestration layer entirely.
            request = SimulationRunRequest(
                strategy_id=args.strategy_id,
                strategy_config={
                    "type": args.strategy_type,
                    "parameters": strategy_parameters,
                },
                dataset_version=args.dataset_version_id,
                random_seed=args.random_seed,
                price_basis=PriceBasis(args.price_basis),
                symbols=_parse_symbols(args.symbols),
                start_date=_parse_date(args.start_date),
                end_date=_parse_date(args.end_date),
                initial_cash=args.initial_cash,
                strict_data_loading=args.strict_data_loading,
                shuffle_timestamp=args.shuffle_timestamps,
            )

            result = simulation_context.simulation_runner.run(request)

        print_header("Run Simulation")
        print_json(
            {
                "status": result.status,
                "run_id": str(result.run_id),
                "experiment_id": result.experiment_id,
                "strategy_id": result.strategy_id,
                "random_seed": args.random_seed,
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


def register_generate_strategies(subparsers) -> None:
    parser = subparsers.add_parser(
        "generate-strategies",
        help="Dry-run the strategy generation engine — no DB, no simulation",
    )
    parser.add_argument(
        "--strategy-type",
        required=True,
        choices=[
            "stub",
            "intentional_loser",
            "random",
            "moving_average_crossover",
            "mean_reversion",
            "momentum",
            "factor_based",
        ],
    )
    parser.add_argument(
        "--parameter-space",
        required=True,
        help="JSON object mapping param name to list of values, e.g. '{\"short_window\": [5,10,20]}'",
    )
    parser.add_argument(
        "--generator",
        choices=["grid", "random"],
        default="grid",
    )
    parser.add_argument(
        "--n-samples",
        type=int,
        default=50,
        help="Number of samples for random generator (ignored for grid)",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Seed for random generator (ignored for grid)",
    )
    parser.add_argument(
        "--show-configs",
        action="store_true",
        help="Print each config in full rather than just the summary",
    )
    parser.set_defaults(func=handle_generate_strategies)


def handle_generate_strategies(args: argparse.Namespace) -> int:
    parameter_space = json.loads(args.parameter_space)

    if not isinstance(parameter_space, dict):
        raise ValueError("--parameter-space must be a JSON object")

    generator: BaseStrategyGenerator
    if args.generator == "grid":
        generator = GridSearchGenerator()
    else:
        generator = RandomSamplingGenerator(
            n_samples=args.n_samples,
            seed=args.random_seed,
        )

    engine = StrategyGenerationEngine(generator=generator)
    configs = engine.generate(
        strategy_type=args.strategy_type,
        parameter_space=parameter_space,
    )

    print_header(f"Strategy generation — {args.generator} — {args.strategy_type}")

    if args.show_configs:
        for config in configs:
            print_json(
                {
                    "strategy_id": config.strategy_id,
                    "config_hash": config.config_hash(),
                    "parameters": config.parameters,
                }
            )

    # Summary
    print_json(
        {
            "generator": args.generator,
            "strategy_type": args.strategy_type,
            "parameter_space": parameter_space,
            "total_generated": len(configs),
            "unique_hashes": len({c.config_hash() for c in configs}),
            "duplicates_skipped": (
                args.n_samples - len(configs) if args.generator == "random" else 0
            ),
            "config_hashes": [c.config_hash() for c in configs],
        }
    )

    return 0
