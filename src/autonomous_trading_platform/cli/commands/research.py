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
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.config.experiment_config import ExperimentConfig
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
from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationResult,
)
from autonomous_trading_platform.research.strategy_generation.generators.base_generator import (
    BaseStrategyGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.evolutionary_generator import (
    EvolutionaryGenerator,
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
from autonomous_trading_platform.strategy.catalog import list_strategy_types
from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.registry import get_registry

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Derived from the strategy catalog — do not edit this list directly.
STRATEGY_TYPE_CHOICES = list_strategy_types()

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


def _parse_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _parameter_spec_to_dict(spec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "type": _enum_value(spec.parameter_type),
        "default": spec.default,
        "description": spec.description,
        "min_value": spec.min_value,
        "max_value": spec.max_value,
        "discrete": spec.discrete,
        "step": spec.step,
        "tunable": spec.tunable,
        "mutation_strategy": spec.mutation_strategy,
    }


def _strategy_definition_to_dict(defn) -> dict[str, Any]:
    defaults = get_registry().get_default_parameters(defn.strategy_type)
    return {
        "strategy_type": defn.strategy_type,
        "display_name": defn.display_name,
        "description": defn.description,
        "family": defn.family.value,
        "implementation_class": defn.implementation_class.__name__,
        "debug": defn.debug,
        "production_ready": defn.production_ready,
        "deterministic": defn.deterministic,
        "default_parameters": defaults,
        "parameter_specs": [_parameter_spec_to_dict(spec) for spec in defn.parameter_specs],
        "parameter_schema": defn.export_parameter_schema(),
        "warmup_bars": defn.compute_warmup_bars(defaults),
        "required_indicators": list(defn.required_indicators),
        "required_persisted_features": list(defn.required_persisted_features),
        "compatibility": {
            "supports_long_only": defn.supports_long_only,
            "supports_shorting": defn.supports_shorting,
            "supports_intraday": defn.supports_intraday,
            "supports_daily": defn.supports_daily,
            "supports_adjusted_prices": defn.supports_adjusted_prices,
            "supports_raw_prices": defn.supports_raw_prices,
        },
    }


def _component_definition_to_dict(defn) -> dict[str, Any]:
    implementation = None
    if defn.implementation is not None:
        implementation = getattr(defn.implementation, "__name__", str(defn.implementation))
    return {
        "component_name": defn.component_name,
        "component_type": defn.component_type.value,
        "display_name": defn.display_name,
        "description": defn.description,
        "implementation": implementation,
        "is_executable": defn.is_executable,
        "metadata_only": defn.metadata_only,
        "required_inputs": list(defn.required_inputs),
        "input_types": dict(defn.input_types),
        "required_price_basis": defn.required_price_basis,
        "required_bar_fields": list(defn.required_bar_fields),
        "parameters": [_parameter_spec_to_dict(spec) for spec in defn.parameter_specs],
        "constraints": list(defn.constraints),
        "optimization_ranges": dict(defn.optimization_ranges),
        "mutation_metadata": dict(defn.mutation_metadata),
        "warmup": {
            "warmup_bars": defn.warmup_bars,
            "warmup_parameter": defn.warmup_parameter,
            "warmup_formula": defn.warmup_formula,
        },
        "output_type": defn.output_type,
        "output_domain": defn.output_domain,
        "compatibility": {
            "compatible_component_types": [item.value for item in defn.compatible_component_types],
            "incompatible_components": list(defn.incompatible_components),
            "allowed_strategy_families": list(defn.allowed_strategy_families),
            "supports_intraday": defn.supports_intraday,
            "supports_daily": defn.supports_daily,
            "supports_raw_prices": defn.supports_raw_prices,
            "supports_adjusted_prices": defn.supports_adjusted_prices,
        },
        "production_ready": defn.production_ready,
        "debug": defn.debug,
        "experimental": defn.experimental,
    }


def _config_to_dict(config) -> dict[str, Any]:
    return {
        "strategy_id": config.strategy_id,
        "type": config.type,
        "parameters": config.parameters,
        "config_hash": config.config_hash(),
    }


def _component_usage(configs) -> dict[str, int]:
    usage: Counter[str] = Counter()
    for config in configs:
        params = config.parameters
        for section in ("indicators", "entry_rules", "filters", "confirmations"):
            for item in params.get(section, []):
                component = item.get("component")
                if component:
                    usage[component] += 1
        aggregator = params.get("aggregator", {}).get("component")
        if aggregator:
            usage[aggregator] += 1
    return dict(sorted(usage.items()))


def _template_usage(configs) -> dict[str, int]:
    templates: Counter[str] = Counter()
    for config in configs:
        template = config.parameters.get("metadata", {}).get("generation_template")
        if template:
            templates[template] += 1
    return dict(sorted(templates.items()))


def _summarize_config_dicts(configs: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_types: Counter[str] = Counter()
    families: Counter[str] = Counter()
    parameter_values: dict[str, list[Any]] = defaultdict(list)
    normalized_configs = []
    registry = get_registry()

    for raw in configs:
        strategy_type = raw.get("type")
        parameters = raw.get("parameters", {})
        if not strategy_type:
            continue
        strategy_types[strategy_type] += 1
        if registry.strategy_exists(strategy_type):
            families[registry.get_definition(strategy_type).family.value] += 1
        normalized_configs.append({"type": strategy_type, "parameters": parameters})
        if isinstance(parameters, dict):
            for key, value in parameters.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    parameter_values[key].append(value)

    parameter_distribution = {}
    for key, values in parameter_values.items():
        parameter_distribution[key] = {
            "min": min(values),
            "max": max(values),
            "distinct_count": len(set(values)),
        }

    return {
        "total_configs": len(configs),
        "strategy_type_distribution": dict(sorted(strategy_types.items())),
        "family_distribution": dict(sorted(families.items())),
        "parameter_distribution": parameter_distribution,
        "component_usage": _component_usage(
            [type("_ConfigView", (), item)() for item in normalized_configs]
        ),
        "composite_template_usage": _template_usage(
            [type("_ConfigView", (), item)() for item in normalized_configs]
        ),
    }


def _generation_options_from_args(args: argparse.Namespace) -> GenerationOptions:
    return GenerationOptions(
        seed=args.random_seed,
        n_samples=args.n_samples,
        population_size=args.population_size,
        generations=args.generations,
        mutation_rate=args.mutation_rate,
        include_debug=args.include_debug,
        include_experimental=args.include_experimental,
        allowed_families=_parse_csv(args.allowed_families),
        excluded_families=_parse_csv(args.excluded_families),
    )


def _generation_artifact(
    *,
    result: GenerationResult,
    generator: str,
    strategy_type: str | None,
    family: str | None,
    parameter_space: dict[str, list] | None,
    options: GenerationOptions,
    include_run_metadata: bool = False,
) -> dict[str, Any]:
    configs = [_config_to_dict(config) for config in result.configs]
    artifact = {
        "artifact_type": "strategy_generation_result",
        "artifact_version": 1,
        "generation": {
            "generator": generator,
            "strategy_type": strategy_type,
            "family": family,
            "parameter_space": parameter_space,
            "options": asdict(options),
        },
        "summary": result.summary.to_dict(),
        "config_hashes": [item["config_hash"] for item in configs],
        "component_usage": _component_usage(result.configs),
        "composite_template_usage": _template_usage(result.configs),
        "configs": configs,
    }
    if include_run_metadata:
        artifact["run_metadata"] = {
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")
        }
    return artifact


def _write_artifact(path: str, payload: dict[str, Any], output_format: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "yaml":
        target.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    else:
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _load_artifact(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw) if path.endswith((".yaml", ".yml")) else json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("--input artifact must contain a mapping")
    return loaded


def _print_payload(title: str, payload: dict[str, Any], output_format: str) -> None:
    print_header(title)
    if output_format == "json":
        print_json(payload)
        return
    if output_format == "yaml":
        print(yaml.safe_dump(payload, sort_keys=False))
        return
    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}: {json.dumps(value, sort_keys=True)}")
        elif isinstance(value, list):
            print(f"{key}: {', '.join(str(item) for item in value)}")
        else:
            print(f"{key}: {value}")


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
    _register_list_strategy_types(research_subparsers)
    _register_inspect_strategy(research_subparsers)
    _register_list_components(research_subparsers)
    _register_inspect_component(research_subparsers)
    _register_generate_strategies(research_subparsers)
    _register_summarize_generated_configs(research_subparsers)


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

    if not isinstance(raw, dict):
        raise ValueError(
            f"YAML file {config_path!r} must contain a mapping, got {type(raw).__name__}"
        )

    staged_pipeline_config = None
    pipeline_raw = raw.get("staged_pipeline_config")

    if pipeline_raw:
        if "stages" not in pipeline_raw:
            raise ValueError("staged_pipeline_config must contain a 'stages' list")
        stages = [
            StageRegistry.load(stage_raw, simulation_context.simulation_runner)
            for stage_raw in pipeline_raw["stages"]
        ]
        staged_pipeline_config = StagedPipelineConfig(stages=stages)

    # Validate all experiment-level fields through ExperimentConfig before
    # constructing ExperimentDefinition. This rejects date.today() fallbacks,
    # empty symbols, invalid enums, and out-of-range values with clear messages.
    # model_validate raises on any violation; we re-raise with the config path.
    fields: dict = {
        "experiment_id": raw.get("experiment_id"),
        "experiment_type": raw.get("experiment_type", "sweep"),
        "description": raw.get("description"),
        "strategy_set": raw.get("strategy_set", []),
        "parameter_grid": raw.get("parameter_grid", []),
        "parameter_space": raw.get("parameter_space"),
        "dataset_version": raw.get("dataset_version"),
        "universe_version": raw.get("universe_version", "v1"),
        "price_basis": raw.get("price_basis"),
        "symbols": raw.get("symbols"),
        "start_date": raw.get("start_date"),
        "end_date": raw.get("end_date"),
        "random_seed": raw.get("random_seed", 42),
        "initial_cash": raw.get("initial_cash", 100_000.0),
        "train_ratio": raw.get("train_ratio"),
        "window_size_days": raw.get("window_size_days"),
        "step_size_days": raw.get("step_size_days"),
        "universe_set": raw.get("universe_set"),
        "universe_resolution_mode": raw.get("universe_resolution_mode"),
    }
    try:
        ExperimentConfig.model_validate(fields)
    except Exception as exc:
        raise ValueError(f"Invalid experiment config in {config_path!r}: {exc}") from exc

    # Construct ExperimentDefinition directly so mypy can verify the return type
    # without relying on Pydantic's Self inference through model_validate.
    from datetime import date as _date

    return ExperimentDefinition(
        experiment_id=fields["experiment_id"],
        experiment_type=ExperimentType(fields["experiment_type"]),
        description=fields["description"],
        strategy_set=fields["strategy_set"] or [],
        parameter_grid=fields["parameter_grid"] or [],
        parameter_space=fields["parameter_space"],
        dataset_version=fields["dataset_version"],
        universe_version=fields["universe_version"],
        price_basis=PriceBasis(fields["price_basis"]),
        symbols=[s.strip().upper() for s in fields["symbols"] if s.strip()],
        start_date=_date.fromisoformat(str(fields["start_date"])),
        end_date=_date.fromisoformat(str(fields["end_date"])),
        random_seed=fields["random_seed"],
        initial_cash=fields["initial_cash"],
        train_ratio=fields["train_ratio"],
        window_size_days=fields["window_size_days"],
        step_size_days=fields["step_size_days"],
        universe_set=fields["universe_set"],
        universe_resolution_mode=fields["universe_resolution_mode"],
        staged_pipeline_config=staged_pipeline_config,
    )


# ---------------------------------------------------------------------------
# registry/component inspection
# ---------------------------------------------------------------------------


def _register_list_strategy_types(subparsers) -> None:
    parser = subparsers.add_parser(
        "list-strategy-types",
        help="List registered strategy types and generation metadata",
        epilog=(
            "Examples:\n"
            "  atp research list-strategy-types\n"
            "  atp research list-strategy-types --family momentum --format json\n"
            "  atp research list-strategy-types --include-debug --include-experimental"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--family", choices=[family.value for family in get_registry().list_families()]
    )
    parser.add_argument("--include-debug", action="store_true")
    parser.add_argument("--include-experimental", action="store_true")
    parser.add_argument("--format", choices=["table", "json", "yaml"], default="table")
    parser.set_defaults(func=handle_list_strategy_types)


def handle_list_strategy_types(args: argparse.Namespace) -> int:
    registry = get_registry()
    rows = []
    for defn in registry.list_definitions():
        if args.family and defn.family.value != args.family:
            continue
        if defn.debug and not args.include_debug:
            continue
        if not defn.production_ready and not args.include_experimental:
            continue
        rows.append(
            {
                "strategy_type": defn.strategy_type,
                "family": defn.family.value,
                "display_name": defn.display_name,
                "production_ready": defn.production_ready,
                "debug": defn.debug,
                "parameter_count": len(defn.parameter_specs),
                "warmup_bars": defn.compute_warmup_bars(),
                "required_indicators": list(defn.required_indicators),
            }
        )

    _print_payload(
        "Strategy Types",
        {"count": len(rows), "strategies": rows},
        args.format,
    )
    return 0


def _register_inspect_strategy(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-strategy",
        help="Inspect one registered strategy definition",
        epilog=(
            "Examples:\n"
            "  atp research inspect-strategy --strategy-type momentum\n"
            "  atp research inspect-strategy --strategy-type moving_average_crossover --format json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--strategy-type", required=True, choices=STRATEGY_TYPE_CHOICES)
    parser.add_argument("--format", choices=["table", "json", "yaml"], default="json")
    parser.set_defaults(func=handle_inspect_strategy)


def handle_inspect_strategy(args: argparse.Namespace) -> int:
    defn = get_registry().get_definition(args.strategy_type)
    _print_payload("Strategy Metadata", _strategy_definition_to_dict(defn), args.format)
    return 0


def _register_list_components(subparsers) -> None:
    parser = subparsers.add_parser(
        "list-components",
        help="List registered strategy components",
        epilog=(
            "Examples:\n"
            "  atp research list-components\n"
            "  atp research list-components --component-type indicator --executable-only\n"
            "  atp research list-components --metadata-only --format json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--component-type", choices=[item.value for item in ComponentType])
    status = parser.add_mutually_exclusive_group()
    status.add_argument("--executable-only", action="store_true")
    status.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--format", choices=["table", "json", "yaml"], default="table")
    parser.set_defaults(func=handle_list_components)


def handle_list_components(args: argparse.Namespace) -> int:
    registry = get_component_registry()
    rows = []
    for defn in registry.list_components():
        if args.component_type and defn.component_type.value != args.component_type:
            continue
        if args.executable_only and not defn.is_executable:
            continue
        if args.metadata_only and not defn.metadata_only:
            continue
        rows.append(
            {
                "component_name": defn.component_name,
                "component_type": defn.component_type.value,
                "display_name": defn.display_name,
                "is_executable": defn.is_executable,
                "metadata_only": defn.metadata_only,
                "parameter_count": len(defn.parameter_specs),
                "required_inputs": list(defn.required_inputs),
                "warmup": defn.warmup_formula or defn.warmup_bars,
            }
        )

    _print_payload(
        "Components",
        {"count": len(rows), "components": rows},
        args.format,
    )
    return 0


def _register_inspect_component(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-component",
        help="Inspect one registered component definition",
        epilog=(
            "Examples:\n"
            "  atp research inspect-component --component-name momentum\n"
            "  atp research inspect-component --component-name volatility_filter --format yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--component-name", required=True)
    parser.add_argument("--format", choices=["table", "json", "yaml"], default="json")
    parser.set_defaults(func=handle_inspect_component)


def handle_inspect_component(args: argparse.Namespace) -> int:
    defn = get_component_registry().get_component_definition(args.component_name)
    _print_payload("Component Metadata", _component_definition_to_dict(defn), args.format)
    return 0


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
        help="Dry-run strategy generation - no DB, no simulation",
        epilog=(
            "Examples:\n"
            "  atp research generate-strategies --strategy-type momentum --generator grid\n"
            "  atp research generate-strategies --strategy-type momentum --generator random --seed 7 --n-samples 10\n"
            "  atp research generate-strategies --strategy-type momentum --generator evolutionary --population-size 8 --generations 2\n"
            "  atp research generate-strategies --composite --summary\n"
            "  atp research generate-strategies --strategy-type momentum --output artifacts/momentum.json\n"
            "  atp research summarize-generated-configs --input artifacts/momentum.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--strategy-type", choices=STRATEGY_TYPE_CHOICES)
    parser.add_argument(
        "--family", choices=[family.value for family in get_registry().list_families()]
    )
    parser.add_argument(
        "--parameter-space",
        default=None,
        help="JSON object mapping param names to lists of values, e.g. '{\"short_window\": [5,10,20]}'",
    )
    parser.add_argument(
        "--parameter-space-file",
        default=None,
        help="Path to JSON/YAML parameter-space mapping. Overrides --parameter-space.",
    )
    parser.add_argument(
        "--generator",
        "--method",
        dest="generator",
        choices=["grid", "random", "evolutionary"],
        default="grid",
        help="grid: exhaustive combinations. random/evolutionary: seed-driven sampling.",
    )
    parser.add_argument(
        "--n-samples",
        type=int,
        default=50,
        help="Number of configs to sample (random generator only, ignored for grid)",
    )
    parser.add_argument(
        "--random-seed",
        "--seed",
        dest="random_seed",
        type=int,
        default=42,
        help="Seed for the random generator — ensures reproducible sampling (ignored for grid)",
    )
    parser.add_argument(
        "--show-configs",
        action="store_true",
        help="Print each config in full before the summary (omit for large spaces)",
    )
    parser.add_argument("--population-size", type=int, default=20)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--mutation-rate", type=float, default=0.25)
    parser.add_argument("--include-debug", action="store_true")
    parser.add_argument("--include-experimental", action="store_true")
    parser.add_argument("--allowed-families", default=None, help="Comma-separated family allowlist")
    parser.add_argument(
        "--excluded-families", default=None, help="Comma-separated family blocklist"
    )
    parser.add_argument(
        "--composite", action="store_true", help="Generate composite_rule templates."
    )
    parser.add_argument("--summary", action="store_true", help="Print summary only")
    parser.add_argument("--verbose", action="store_true", help="Include rejected/duplicate details")
    parser.add_argument("--output", default=None, help="Write generation artifact to this path")
    parser.add_argument(
        "--output-format",
        "--format",
        dest="output_format",
        choices=["json", "yaml"],
        default="json",
    )
    parser.add_argument(
        "--include-run-metadata",
        action="store_true",
        help="Include generated_at run metadata in exported artifact",
    )
    parser.set_defaults(func=handle_generate_strategies)


def _register_summarize_generated_configs(subparsers) -> None:
    parser = subparsers.add_parser(
        "summarize-generated-configs",
        help="Summarize an exported strategy generation artifact",
        epilog=(
            "Examples:\n"
            "  atp research summarize-generated-configs --input artifacts/momentum.json\n"
            "  atp research summarize-generated-configs --input artifacts/composite.yaml --format yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", required=True, help="JSON/YAML artifact from generate-strategies"
    )
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.add_argument("--show-hashes", action="store_true")
    parser.set_defaults(func=handle_summarize_generated_configs)


def handle_summarize_generated_configs(args: argparse.Namespace) -> int:
    artifact = _load_artifact(args.input)
    configs = artifact.get("configs", [])
    if not isinstance(configs, list):
        raise ValueError("Artifact field 'configs' must be a list")

    summary = _summarize_config_dicts(configs)
    generation_summary = artifact.get("summary", {})
    if isinstance(generation_summary, dict):
        summary.update(
            {
                "generated_count": generation_summary.get("generated_count"),
                "accepted_count": generation_summary.get("accepted_count"),
                "duplicate_count": generation_summary.get("duplicate_count"),
                "rejected_count": generation_summary.get("rejected_count"),
                "rejection_reasons": generation_summary.get("rejection_reasons", {}),
            }
        )
    summary["component_usage"] = artifact.get("component_usage", summary["component_usage"])
    summary["composite_template_usage"] = artifact.get(
        "composite_template_usage", summary["composite_template_usage"]
    )
    if args.show_hashes:
        summary["config_hashes"] = artifact.get(
            "config_hashes",
            [item.get("config_hash") for item in configs if isinstance(item, dict)],
        )

    _print_payload("Generated Config Summary", summary, args.format)
    return 0


def handle_generate_strategies(args: argparse.Namespace) -> int:
    """Handle the generate-strategies subcommand.

    Builds the chosen generator, wraps it in StrategyGenerationEngine (which
    handles deduplication via config_hash), and prints results.

    The summary always shows total_generated and unique_hashes — if these
    differ from your expectations the parameter space or generator config
    needs adjustment before running a full experiment.
    """
    if args.parameter_space_file:
        loaded_space = _load_artifact(args.parameter_space_file)
        parameter_space = loaded_space.get("parameter_space", loaded_space)
    else:
        parameter_space = json.loads(args.parameter_space) if args.parameter_space else None

    if parameter_space is not None and not isinstance(parameter_space, dict):
        raise ValueError("--parameter-space must be a JSON object")
    if args.composite:
        args.strategy_type = "composite_rule"
    if not args.strategy_type and not args.family:
        raise ValueError("Provide --strategy-type, --family, or --composite")

    # Select generator — grid is deterministic and exhaustive, random samples
    # n_samples from the space and relies on the engine to deduplicate.
    generator: BaseStrategyGenerator
    if args.generator == "grid":
        generator = GridSearchGenerator()
    elif args.generator == "random":
        generator = RandomSamplingGenerator(
            n_samples=args.n_samples,
            seed=args.random_seed,
        )
    else:
        generator = EvolutionaryGenerator(
            seed=args.random_seed,
            population_size=args.population_size,
            generations=args.generations,
            mutation_rate=args.mutation_rate,
        )

    engine = StrategyGenerationEngine(generator=generator)
    options = _generation_options_from_args(args)
    if args.composite:
        result = engine.generate_composite(method=args.generator, options=options)
    elif args.family:
        result = engine.generate_for_family(args.family, method=args.generator, options=options)
    else:
        result = engine.generate_result(
            strategy_type=args.strategy_type,
            method=args.generator,
            parameter_space=parameter_space,
            options=options,
        )
    configs = result.configs
    print_header(f"Strategy generation - {args.generator} - {args.family or args.strategy_type}")

    if args.show_configs and not args.summary:
        for config in result.configs:
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
            "family": args.family,
            "generated_count": result.summary.generated_count,
            "accepted_count": result.summary.accepted_count,
            "duplicate_count": result.summary.duplicate_count,
            "rejected_count": result.summary.rejected_count,
            "rejection_reasons": dict(result.summary.rejection_reasons),
            "unique_hashes": len({c.config_hash() for c in configs}),
            "strategy_type_distribution": dict(result.summary.strategy_type_distribution),
            "family_distribution": dict(result.summary.family_distribution),
            # duplicates_skipped is only meaningful for random — grid never
            "config_hashes": [c.config_hash() for c in configs],
        }
    )

    artifact = _generation_artifact(
        result=result,
        generator=args.generator,
        strategy_type=args.strategy_type,
        family=args.family,
        parameter_space=parameter_space,
        options=options,
        include_run_metadata=args.include_run_metadata,
    )
    print_json(
        {
            "component_usage": artifact["component_usage"],
            "composite_template_usage": artifact["composite_template_usage"],
        }
    )
    if args.verbose:
        print_json(
            {
                "rejected_details": artifact["summary"]["rejected_details"],
                "duplicate_details": artifact["summary"]["duplicate_details"],
            }
        )
    if args.output:
        _write_artifact(args.output, artifact, args.output_format)
        print_json({"artifact_path": args.output, "artifact_format": args.output_format})

    return 0
