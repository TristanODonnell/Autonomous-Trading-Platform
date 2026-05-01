"""CLI commands for the research domain.

Three subcommands, each targeting a different layer of the research stack:

    run-simulation      Ad-hoc / debug — bypasses orchestration entirely.
                        Runs a single strategy directly against SimulationRunner.
                        Use this to quickly validate a strategy or data window
                        without touching the experiment layer.

    run-experiment      Full pipeline — strategy generation → experiment
                        orchestration → simulation. Use this when you want to
                        sweep a parameter space or run a structured experiment
                        that gets persisted to the DB.

    generate-strategies Dry-run the strategy generation engine with no DB and
                        no simulation. Use this to verify that a parameter space
                        produces the expected configs and hashes before committing
                        to a full experiment run.
"""

from __future__ import annotations

import argparse
import json
from datetime import date

import yaml

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
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

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Canonical list of supported strategy types. Referenced by every subcommand
# that accepts --strategy-type so additions only need to happen in one place.
STRATEGY_TYPE_CHOICES = [
    "stub",
    "intentional_loser",
    "random",
    "moving_average_crossover",
    "mean_reversion",
    "momentum",
    "factor_based",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_symbols(raw: str) -> list[str]:
    """Split a comma-separated symbol string into a normalised list.

    Strips whitespace and uppercases each symbol. Raises if the result is empty
    so callers always receive at least one symbol.
    """
    symbols = [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")
    return symbols


def _parse_date(raw: str) -> date:
    """Parse an ISO-8601 date string (YYYY-MM-DD) into a date object."""
    return date.fromisoformat(raw)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register all research subcommands under the 'research' parent parser."""
    research_parser = subparsers.add_parser("research", help="Research operations")
    research_subparsers = research_parser.add_subparsers(
        dest="research_command",
        required=True,
    )

    _register_run_simulation(research_subparsers)
    _register_run_experiment(research_subparsers)
    _register_generate_strategies(research_subparsers)


# ---------------------------------------------------------------------------
# run-simulation
# ---------------------------------------------------------------------------


def _register_run_simulation(subparsers) -> None:
    """Register the run-simulation subcommand.

    Intended for ad-hoc debugging of a single strategy against a data window.
    When --experiment-id is omitted the run bypasses the orchestration layer
    entirely and calls SimulationRunner directly — no DB writes for experiments
    or strategy configs, fastest possible feedback loop.

    When --experiment-id is supplied it routes through
    ExperimentOrchestrationService as an AB experiment with a single strategy,
    which does persist to the DB. Useful for recording a one-off run without
    setting up a full parameter sweep.
    """
    parser = subparsers.add_parser(
        "run-simulation",
        help="Ad-hoc single-strategy simulation — bypasses orchestration by default",
    )
    parser.add_argument("--dataset-version-id", required=True)
    parser.add_argument(
        "--price-basis",
        required=True,
        choices=["raw", "adjusted"],
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols, e.g. SPY,AAPL,MSFT",
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--strategy-type",
        required=True,
        choices=STRATEGY_TYPE_CHOICES,
        help="Strategy type to run",
    )
    parser.add_argument(
        "--strategy-parameters",
        default="{}",
        help='JSON string of strategy parameters, e.g. \'{"short_window": 10, "long_window": 30}\'',
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        required=True,
        help="Fixed random seed — ensures identical replays given identical inputs",
    )
    parser.add_argument(
        "--shuffle-timestamps",
        action="store_true",
        help="Shuffle timestamps to break temporal structure (diagnostic test only)",
    )
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="If supplied, routes through ExperimentOrchestrationService and persists to DB",
    )
    parser.add_argument(
        "--universe-version",
        default="v1",
        help="Universe version tag (only used when --experiment-id is supplied)",
    )
    parser.add_argument(
        "--strict-data-loading",
        action="store_true",
        help="Fail if any requested symbol has no bars in the requested window",
    )
    parser.set_defaults(func=handle_run_simulation)


def handle_run_simulation(args: argparse.Namespace) -> int:
    """Handle the run-simulation subcommand.

    Two execution paths:
      - With --experiment-id: routes through ExperimentOrchestrationService.
        Persists the experiment and run to the DB. Treats the single strategy
        as an AB experiment so the orchestrator accepts it.
      - Without --experiment-id: calls SimulationRunner directly. Nothing is
        persisted. Fastest path for iterating on a strategy or data issue.
    """
    session = get_session()

    try:
        simulation_context = build_simulation_context(session=session)

        strategy_parameters = json.loads(args.strategy_parameters)

        if not isinstance(strategy_parameters, dict):
            raise ValueError("--strategy-parameters must be a JSON object")

        if args.experiment_id:
            # Orchestrated path — persists to DB via ExperimentOrchestrationService.
            # experiment_type=AB because we have exactly one strategy and no
            # parameter sweep; AB is the simplest valid type for a single run.
            plan = ExperimentDefinition(
                experiment_id=args.experiment_id,
                experiment_type=ExperimentType.AB,
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
            )

            results, _filter_outputs = (
                simulation_context.experiment_orchestration_service.run_experiment(plan)
            )
            result = results[0]

        else:
            # Direct path — bypasses orchestration, no DB writes for this run.
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


# ---------------------------------------------------------------------------
# run-experiment
# ---------------------------------------------------------------------------


def _register_run_experiment(subparsers) -> None:
    """Register the run-experiment subcommand.

    Full pipeline command — strategy generation feeds into experiment
    orchestration which feeds into simulation. Always persists to the DB.

    When --parameter-space is supplied the StrategyGenerationEngine expands
    the space into individual StrategyConfigs (grid search by default) and
    runs a simulation for each. When omitted, the single strategy defined by
    --strategy-type and --strategy-parameters is run as-is.
    """

    parser = subparsers.add_parser(
        "run-experiment",
        help="Full pipeline — strategy generation → orchestration → simulation",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="Path to a YAML experiment config file. When supplied, all other flags are ignored.",
    )

    parser.add_argument("--experiment-id", required=False, default=None)
    parser.add_argument("--dataset-version-id", required=False, default=None)
    parser.add_argument("--price-basis", required=False, default=None, choices=["raw", "adjusted"])
    parser.add_argument(
        "--symbols",
        required=False,
        default=None,
        help="Comma-separated list of symbols, e.g. SPY,AAPL,MSFT",
    )
    parser.add_argument("--start-date", required=False, default=None)
    parser.add_argument("--end-date", required=False, default=None)
    parser.add_argument(
        "--strategy-type",
        required=False,
        default=None,
        choices=STRATEGY_TYPE_CHOICES,
    )
    parser.add_argument("--random-seed", type=int, required=False, default=None)
    parser.add_argument(
        "--parameter-space",
        default=None,
        help=(
            "JSON object mapping param names to lists of values to sweep. "
            "If omitted, --strategy-parameters is used as-is with no generation. "
            'e.g. \'{"short_window": [5,10,20], "long_window": [50,100]}\''
        ),
    )
    parser.add_argument(
        "--strategy-parameters",
        default="{}",
        help="Base parameters for the strategy (used as-is when --parameter-space is omitted)",
    )
    parser.add_argument(
        "--experiment-type",
        default="sweep",
        choices=["ab", "sweep", "time_segmentation", "rolling_window", "cross_universe"],
        help="Experiment type controlling how windows and strategies are expanded",
    )
    parser.add_argument("--universe-version", default="v1")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.set_defaults(func=handle_run_experiment)


def handle_run_experiment(args: argparse.Namespace) -> int:
    session = get_session()
    if not args.config:
        # inline path — these flags are required
        missing = [
            f
            for f, v in [
                ("--experiment-id", args.experiment_id),
                ("--dataset-version-id", args.dataset_version_id),
                ("--price-basis", args.price_basis),
                ("--symbols", args.symbols),
                ("--start-date", args.start_date),
                ("--end-date", args.end_date),
                ("--strategy-type", args.strategy_type),
                ("--random-seed", args.random_seed),
            ]
            if v is None
        ]
        if missing:
            raise SystemExit(
                f"error: the following arguments are required when --config is not supplied: {', '.join(missing)}"
            )
    try:
        simulation_context = build_simulation_context(session=session)

        if args.config:
            plan = _load_experiment_from_yaml(args.config, simulation_context)
        else:
            strategy_parameters = json.loads(args.strategy_parameters)
            parameter_space = json.loads(args.parameter_space) if args.parameter_space else None

            plan = ExperimentDefinition(
                experiment_id=args.experiment_id,
                experiment_type=ExperimentType(args.experiment_type),
                description=None,
                strategy_set=[
                    {
                        "strategy_id": f"{args.strategy_type}__base",
                        "type": args.strategy_type,
                        "parameters": strategy_parameters,
                    }
                ],
                parameter_grid=[],
                parameter_space=parameter_space,
                dataset_version=args.dataset_version_id,
                universe_version=args.universe_version,
                price_basis=PriceBasis(args.price_basis),
                symbols=_parse_symbols(args.symbols),
                start_date=_parse_date(args.start_date),
                end_date=_parse_date(args.end_date),
                random_seed=args.random_seed,
                initial_cash=args.initial_cash,
            )

        if plan.staged_pipeline_config is not None:
            pipeline_result = (
                simulation_context.experiment_orchestration_service.run_staged_experiment(plan)
            )

            print_header(f"Experiment — {plan.experiment_id} (staged pipeline)")
            print_json(
                {
                    "experiment_id": plan.experiment_id,
                    "total_stages": len(pipeline_result.stage_results),
                    "final_survivors": len(pipeline_result.final_survivors),
                    "stages": [
                        {
                            "stage": sr.stage_name,
                            "entered": sr.n_entered,
                            "passed": sr.n_passed,
                            "failed": sr.n_failed,
                            "filter_results": [
                                {
                                    "strategy_id": o.strategy_id,
                                    "passed": o.filter_result.passed,
                                    "score": round(o.score.score, 4) if o.score else None,
                                    "failures": o.filter_result.failures,
                                }
                                for o in sr.filter_outputs
                            ],
                        }
                        for sr in pipeline_result.stage_results
                    ],
                }
            )
        else:
            results, filter_outputs = (
                simulation_context.experiment_orchestration_service.run_experiment(plan)
            )
            print_header(f"Experiment — {plan.experiment_id}")
            print_json(
                {
                    "experiment_id": plan.experiment_id,
                    "total_runs": len(results),
                    "total_passed": len([o for o in filter_outputs if o.filter_result.passed]),
                    "total_failed": len([o for o in filter_outputs if not o.filter_result.passed]),
                    "runs": [
                        {
                            "status": r.status,
                            "run_id": str(r.run_id),
                            "strategy_id": r.strategy_id,
                            "trade_count": r.trade_count,
                            "equity_points": r.equity_points,
                        }
                        for r in results
                    ],
                    "filter_results": [
                        {
                            "strategy_id": o.strategy_id,
                            "passed": o.filter_result.passed,
                            "score": round(o.score.score, 4) if o.score is not None else None,
                            "failures": o.filter_result.failures,
                        }
                        for o in filter_outputs
                    ],
                }
            )

        return 0

    finally:
        session.close()


def _load_experiment_from_yaml(
    config_path: str,
    simulation_context,
) -> ExperimentDefinition:
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    staged_pipeline_config = None
    pipeline_raw = raw.get("staged_pipeline_config")

    if pipeline_raw:
        stages = [
            StageRegistry.load(stage_raw, simulation_context.simulation_runner)
            for stage_raw in pipeline_raw["stages"]
        ]
        staged_pipeline_config = StagedPipelineConfig(stages=stages)

    return ExperimentDefinition(
        experiment_id=raw["experiment_id"],
        experiment_type=ExperimentType(raw.get("experiment_type", "sweep")),
        description=raw.get("description"),
        strategy_set=raw.get("strategy_set", []),
        parameter_grid=raw.get("parameter_grid", []),
        parameter_space=raw.get("parameter_space"),
        dataset_version=raw["dataset_version"],
        universe_version=raw.get("universe_version", "v1"),
        price_basis=PriceBasis(raw["price_basis"]),
        symbols=raw.get("symbols", []),
        start_date=date.fromisoformat(raw["start_date"]) if "start_date" in raw else date.today(),
        end_date=date.fromisoformat(raw["end_date"]) if "end_date" in raw else date.today(),
        random_seed=raw.get("random_seed", 42),
        initial_cash=raw.get("initial_cash", 100_000.0),
        staged_pipeline_config=staged_pipeline_config,
    )


# ---------------------------------------------------------------------------
# generate-strategies
# ---------------------------------------------------------------------------


def _register_generate_strategies(subparsers) -> None:
    """Register the generate-strategies subcommand.

    Dry-run only — no DB access, no simulation. Instantiates the
    StrategyGenerationEngine with the chosen generator and prints the configs
    it would produce. Use this to sanity-check a parameter space before
    committing to a full experiment run.

    Useful for verifying:
      - Total config count (grid: deterministic, random: capped by unique combos)
      - Hash uniqueness (dedup is working)
      - Parameter combinations look correct before running simulations
    """
    parser = subparsers.add_parser(
        "generate-strategies",
        help="Dry-run strategy generation — no DB, no simulation",
    )
    parser.add_argument(
        "--strategy-type",
        required=True,
        choices=STRATEGY_TYPE_CHOICES,
    )
    parser.add_argument(
        "--parameter-space",
        required=True,
        help="JSON object mapping param names to lists of values, e.g. '{\"short_window\": [5,10,20]}'",
    )
    parser.add_argument(
        "--generator",
        choices=["grid", "random"],
        default="grid",
        help="grid: exhaustive combinations. random: sample n configs from the space.",
    )
    parser.add_argument(
        "--n-samples",
        type=int,
        default=50,
        help="Number of configs to sample (random generator only, ignored for grid)",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Seed for the random generator — ensures reproducible sampling (ignored for grid)",
    )
    parser.add_argument(
        "--show-configs",
        action="store_true",
        help="Print each config in full before the summary (omit for large spaces)",
    )
    parser.set_defaults(func=handle_generate_strategies)


def handle_generate_strategies(args: argparse.Namespace) -> int:
    """Handle the generate-strategies subcommand.

    Builds the chosen generator, wraps it in StrategyGenerationEngine (which
    handles deduplication via config_hash), and prints results.

    The summary always shows total_generated and unique_hashes — if these
    differ from your expectations the parameter space or generator config
    needs adjustment before running a full experiment.
    """
    parameter_space = json.loads(args.parameter_space)

    if not isinstance(parameter_space, dict):
        raise ValueError("--parameter-space must be a JSON object")

    # Select generator — grid is deterministic and exhaustive, random samples
    # n_samples from the space and relies on the engine to deduplicate.
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

    # Summary — always printed regardless of --show-configs
    print_json(
        {
            "generator": args.generator,
            "strategy_type": args.strategy_type,
            "parameter_space": parameter_space,
            "total_generated": len(configs),
            "unique_hashes": len({c.config_hash() for c in configs}),
            # duplicates_skipped is only meaningful for random — grid never
            # produces duplicates since it iterates the sorted cartesian product
            "duplicates_skipped": (
                args.n_samples - len(configs) if args.generator == "random" else 0
            ),
            "config_hashes": [c.config_hash() for c in configs],
        }
    )

    return 0
