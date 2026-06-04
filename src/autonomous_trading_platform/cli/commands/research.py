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
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
)
from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.black_litterman.black_litterman_research_service import (
    BlackLittermanInput,
    BlackLittermanResearchService,
)
from autonomous_trading_platform.research.cache.simulation_result_cache import (
    SimulationResultCache,
)
from autonomous_trading_platform.research.cache.strategy_generation_cache import (
    StrategyGenerationCache,
)
from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpointIdentity,
)
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
)
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
from autonomous_trading_platform.research.simulation.services.feature_dependency_resolver_service import (
    FeatureDependencyError,
    FeatureDependencyResolverService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
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
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.repositories.core.feature_dataset_versions_repository import (
    FeatureDatasetVersionsRepository,
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


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be numeric") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _pct(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be numeric") from exc
    if value < 0 or value > 1:
        raise argparse.ArgumentTypeError("value must be between 0 and 1")
    return value


def _bounded_limit(raw: str) -> int:
    value = _positive_int(raw)
    if value > 1000:
        raise argparse.ArgumentTypeError("value must be <= 1000")
    return value


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


def _emit_artifact_if_requested(
    *,
    payload: dict[str, Any],
    output: str | None,
    output_format: str = "json",
) -> None:
    if output:
        _write_artifact(output, payload, output_format)


def _cache_path(cache_name: str, raw_path: str | None) -> Path:
    if raw_path:
        return Path(raw_path)
    suffix = {
        "strategy-generation": "strategy_generation_cache.json",
        "simulation-results": "simulation_result_cache.json",
    }[cache_name]
    return Path("artifacts/research/cache") / suffix


def _cache_for_name(cache_name: str, raw_path: str | None):
    path = _cache_path(cache_name, raw_path)
    if cache_name == "strategy-generation":
        return StrategyGenerationCache(path), path
    if cache_name == "simulation-results":
        return SimulationResultCache(path), path
    raise ValueError(f"Unsupported cache: {cache_name}")


def _load_black_litterman_config(path: str) -> BlackLittermanInput:
    raw = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw) if path.endswith((".yaml", ".yml")) else json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Black-Litterman config must contain a mapping")

    payload = dict(loaded)
    if "benchmark_weights" not in payload and isinstance(payload.get("benchmark"), dict):
        payload["benchmark_weights"] = payload["benchmark"]
    if "benchmark_weights" not in payload:
        raise ValueError("Black-Litterman config requires benchmark_weights")
    if "universe" not in payload and isinstance(payload.get("assets"), list):
        payload["universe"] = payload["assets"]
    payload.setdefault("run_context", "research_cli")
    return BlackLittermanInput(**payload)


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
    _register_experiment_catalog(research_subparsers)
    _register_validate_config(research_subparsers)
    _register_plan_experiment(research_subparsers)
    _register_run_inspection(research_subparsers)
    _register_list_strategy_types(research_subparsers)
    _register_inspect_strategy(research_subparsers)
    _register_list_components(research_subparsers)
    _register_inspect_component(research_subparsers)
    _register_generate_strategies(research_subparsers)
    _register_summarize_generated_configs(research_subparsers)
    _register_inspect_checkpoints(research_subparsers)
    _register_plan_restart(research_subparsers)
    _register_resume_experiment(research_subparsers)
    _register_black_litterman(research_subparsers)
    _register_feature_dependencies(research_subparsers)
    _register_cache_commands(research_subparsers)
    _register_research_validation(research_subparsers)
    _register_regime_analysis(research_subparsers)
    _register_intelligence_commands(research_subparsers)
    _register_calibration(research_subparsers)
    _register_cost_model(research_subparsers)


# ---------------------------------------------------------------------------
# experiment catalog / planning
# ---------------------------------------------------------------------------


def _register_experiment_catalog(subparsers) -> None:
    list_parser = subparsers.add_parser(
        "list-experiments",
        help="List research experiment records",
    )
    list_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    list_parser.set_defaults(func=handle_list_experiments)

    inspect_parser = subparsers.add_parser(
        "inspect-experiment",
        help="Inspect one research experiment",
    )
    inspect_parser.add_argument("--experiment-id", required=True)
    inspect_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    inspect_parser.set_defaults(func=handle_inspect_experiment)

    cancel_parser = subparsers.add_parser(
        "cancel-experiment",
        help="Cancel a pending, queued, or running research experiment",
    )
    cancel_parser.add_argument("--experiment-id", required=True)
    cancel_parser.add_argument("--reason", required=True)
    cancel_parser.add_argument("--actor", default=None)
    cancel_parser.add_argument("--dry-run", action="store_true")
    cancel_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    cancel_parser.set_defaults(func=handle_cancel_experiment)

    strategies_parser = subparsers.add_parser(
        "list-experiment-strategies",
        help="List strategy outcomes for an experiment",
    )
    strategies_parser.add_argument("--experiment-id", required=True)
    strategies_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    strategies_parser.set_defaults(func=handle_list_experiment_strategies)


def _register_validate_config(subparsers) -> None:
    parser = subparsers.add_parser(
        "validate-config",
        help="Validate an experiment YAML/JSON config without running it",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.set_defaults(func=handle_validate_config)


def _register_plan_experiment(subparsers) -> None:
    parser = subparsers.add_parser(
        "plan-experiment",
        help="Expand an experiment config or inline experiment args without execution",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument("--dataset-version-id", default=None)
    parser.add_argument("--price-basis", choices=["raw", "adjusted"], default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--strategy-type", choices=STRATEGY_TYPE_CHOICES, default=None)
    parser.add_argument("--strategy-parameters", default="{}")
    parser.add_argument("--parameter-space", default=None)
    parser.add_argument("--experiment-type", default="sweep")
    parser.add_argument("--universe-version", default="v1")
    parser.add_argument("--initial-cash", type=_positive_float, default=100_000.0)
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--execution-mode", choices=["serial", "parallel"], default="serial")
    parser.add_argument("--max-workers", type=_positive_int, default=1)
    parser.add_argument("--base-seed", type=int, default=None)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", choices=["json", "yaml"], default="json")
    parser.set_defaults(func=handle_plan_experiment)


def _register_run_inspection(subparsers) -> None:
    list_parser = subparsers.add_parser(
        "list-runs",
        help="List simulation runs, optionally scoped to an experiment",
    )
    list_parser.add_argument("--experiment-id", default=None)
    list_parser.add_argument("--limit", type=_bounded_limit, default=50)
    list_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    list_parser.set_defaults(func=handle_list_runs)

    inspect_parser = subparsers.add_parser(
        "inspect-run",
        help="Inspect metrics, lineage, and config for one simulation run",
    )
    inspect_parser.add_argument("--run-id", required=True)
    inspect_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    inspect_parser.set_defaults(func=handle_inspect_run)


def handle_list_experiments(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        svc = ExperimentCatalogService(session=session)
        rows = svc.list_experiments()
        _print_payload(
            "Research Experiments", {"count": len(rows), "experiments": rows}, args.format
        )
        return 0
    finally:
        session.close()


def handle_inspect_experiment(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        svc = ExperimentCatalogService(session=session)
        try:
            payload = svc.get_experiment_detail(experiment_id=args.experiment_id)
        except LookupError as exc:
            _print_payload(
                "Research Experiment",
                {"found": False, "experiment_id": args.experiment_id, "error": str(exc)},
                args.format,
            )
            return 1
        payload["found"] = True
        _print_payload("Research Experiment", payload, args.format)
        return 0
    finally:
        session.close()


def handle_cancel_experiment(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        svc = ExperimentCatalogService(session=session)
        if args.dry_run:
            try:
                detail = svc.get_experiment_detail(experiment_id=args.experiment_id)
            except LookupError as exc:
                _print_payload(
                    "Cancel Research Experiment",
                    {"dry_run": True, "would_cancel": False, "error": str(exc)},
                    args.format,
                )
                return 1
            _print_payload(
                "Cancel Research Experiment",
                {
                    "dry_run": True,
                    "would_cancel": detail["status"] in ("pending", "queued", "running"),
                    "experiment_id": args.experiment_id,
                    "current_status": detail["status"],
                    "reason": args.reason,
                    "actor": args.actor,
                },
                args.format,
            )
            return 0
        try:
            svc.cancel_experiment(experiment_id=args.experiment_id)
        except (LookupError, ValueError) as exc:
            session.rollback()
            _print_payload(
                "Cancel Research Experiment",
                {"dry_run": False, "status": "error", "error": str(exc)},
                args.format,
            )
            return 1
        session.commit()
        _print_payload(
            "Cancel Research Experiment",
            {
                "dry_run": False,
                "status": "cancelled",
                "experiment_id": args.experiment_id,
                "reason": args.reason,
                "actor": args.actor,
            },
            args.format,
        )
        return 0
    finally:
        session.close()


def handle_list_experiment_strategies(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        svc = ExperimentCatalogService(session=session)
        try:
            strategies = svc.get_experiment_strategies(experiment_id=args.experiment_id)
        except LookupError as exc:
            _print_payload(
                "Experiment Strategies",
                {"found": False, "experiment_id": args.experiment_id, "error": str(exc)},
                args.format,
            )
            return 1
        _print_payload(
            "Experiment Strategies",
            {
                "found": True,
                "experiment_id": args.experiment_id,
                "count": len(strategies),
                "strategies": strategies,
            },
            args.format,
        )
        return 0
    finally:
        session.close()


def handle_validate_config(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        try:
            context = build_simulation_context(session=session)
            plan = _load_experiment_from_yaml(args.config, context, args=None)
            payload = {"valid": True, "plan": _experiment_plan_payload(plan)}
            rc = 0
        except Exception as exc:
            payload = {"valid": False, "config": args.config, "error": str(exc)}
            rc = 1
        _print_payload("Validate Research Config", payload, args.format)
        return rc
    finally:
        session.close()


def handle_plan_experiment(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        context = build_simulation_context(session=session)
        plan = _plan_from_args(args, context)
        payload = {
            "dry_run": True,
            "plan": _experiment_plan_payload(plan),
            "work_units": _experiment_work_units(plan),
        }
        _emit_artifact_if_requested(payload=payload, output=args.output, output_format=args.format)
        _print_payload("Research Experiment Plan", payload, args.format)
        return 0
    finally:
        session.close()


def handle_list_runs(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        stmt = select(SimulationRuns).order_by(SimulationRuns.start_time.desc()).limit(args.limit)
        if args.experiment_id:
            stmt = stmt.where(SimulationRuns.experiment_id == args.experiment_id)
        runs = list(session.scalars(stmt).all())
        payload = {
            "experiment_id": args.experiment_id,
            "count": len(runs),
            "runs": [_simulation_run_payload(run, include_config=False) for run in runs],
        }
        _print_payload("Research Simulation Runs", payload, args.format)
        return 0
    finally:
        session.close()


def handle_inspect_run(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        run = session.get(SimulationRuns, args.run_id)
        if run is None:
            _print_payload(
                "Research Simulation Run",
                {"found": False, "run_id": args.run_id},
                args.format,
            )
            return 1
        metrics = session.scalars(
            select(MetricsSummary).where(MetricsSummary.run_id == args.run_id).limit(1)
        ).one_or_none()
        payload = _simulation_run_payload(run, include_config=True)
        payload["found"] = True
        payload["metrics"] = _metrics_payload(metrics)
        _print_payload("Research Simulation Run", payload, args.format)
        return 0
    finally:
        session.close()


def _register_feature_dependencies(subparsers) -> None:
    parser = subparsers.add_parser(
        "resolve-feature-dependencies",
        help="Resolve persisted feature dependencies required by a strategy",
    )
    parser.add_argument("--strategy-type", required=True, choices=STRATEGY_TYPE_CHOICES)
    parser.add_argument("--strategy-parameters", default="{}")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--dataset-version-id", required=True)
    parser.add_argument("--price-basis", required=True, choices=["raw", "adjusted"])
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.set_defaults(func=handle_resolve_feature_dependencies)


def _register_cache_commands(subparsers) -> None:
    inspect_parser = subparsers.add_parser("inspect-cache", help="Inspect a research cache")
    inspect_parser.add_argument(
        "--cache",
        required=True,
        choices=["strategy-generation", "simulation-results"],
    )
    inspect_parser.add_argument("--cache-store", default=None)
    inspect_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    inspect_parser.set_defaults(func=handle_inspect_cache)

    validate_parser = subparsers.add_parser("validate-cache", help="Validate research cache shape")
    validate_parser.add_argument(
        "--cache",
        required=True,
        choices=["strategy-generation", "simulation-results"],
    )
    validate_parser.add_argument("--cache-store", default=None)
    validate_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    validate_parser.set_defaults(func=handle_validate_cache)

    clear_parser = subparsers.add_parser("clear-cache", help="Clear a research cache")
    clear_parser.add_argument(
        "--cache",
        required=True,
        choices=["strategy-generation", "simulation-results"],
    )
    clear_parser.add_argument("--cache-store", default=None)
    clear_parser.add_argument("--dry-run", action="store_true", default=True)
    clear_parser.add_argument("--execute", action="store_true")
    clear_parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    clear_parser.set_defaults(func=handle_clear_cache)


def _register_research_validation(subparsers) -> None:
    parser = subparsers.add_parser(
        "run-validation",
        help="Validate a research validation config and emit a plan artifact",
    )
    parser.add_argument("--type", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", choices=["json", "yaml"], default="json")
    parser.set_defaults(func=handle_run_validation)


def _register_regime_analysis(subparsers) -> None:
    parser = subparsers.add_parser(
        "analyze-regimes",
        help="Create a regime-analysis request artifact for an experiment",
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", choices=["json", "yaml"], default="json")
    parser.set_defaults(func=handle_analyze_regimes)


def _register_intelligence_commands(subparsers) -> None:
    parser = subparsers.add_parser("intelligence", help="Research intelligence operations")
    intelligence_subparsers = parser.add_subparsers(
        dest="research_intelligence_command",
        required=True,
    )

    rank_parser = intelligence_subparsers.add_parser(
        "rank-candidates",
        help="Rank candidate strategies for an experiment",
    )
    rank_parser.add_argument("--experiment-id", required=True)
    rank_parser.add_argument("--output", default=None)
    rank_parser.add_argument("--format", choices=["json", "yaml"], default="json")
    rank_parser.set_defaults(func=handle_intelligence_rank_candidates)

    cluster_parser = intelligence_subparsers.add_parser(
        "cluster-strategies",
        help="Export candidate strategy clustering input for an experiment",
    )
    cluster_parser.add_argument("--experiment-id", required=True)
    cluster_parser.add_argument("--output", default=None)
    cluster_parser.add_argument("--format", choices=["json", "yaml"], default="json")
    cluster_parser.set_defaults(func=handle_intelligence_cluster_strategies)

    robustness_parser = intelligence_subparsers.add_parser(
        "predict-robustness",
        help="Estimate robustness inputs for candidate strategies",
    )
    robustness_parser.add_argument("--experiment-id", required=True)
    robustness_parser.add_argument("--output", default=None)
    robustness_parser.add_argument("--format", choices=["json", "yaml"], default="json")
    robustness_parser.set_defaults(func=handle_intelligence_predict_robustness)


def _register_calibration(subparsers) -> None:
    parser = subparsers.add_parser(
        "calibrate-slippage",
        help="Validate fills-source input and write a slippage calibration request artifact",
    )
    parser.add_argument("--fills-source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=["json", "yaml"], default="json")
    parser.set_defaults(func=handle_calibrate_slippage)


def _register_cost_model(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-cost-model",
        help="Preview simulated cost model assumptions",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.set_defaults(func=handle_inspect_cost_model)


def handle_resolve_feature_dependencies(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        strategy_parameters = json.loads(args.strategy_parameters)
        if not isinstance(strategy_parameters, dict):
            raise ValueError("--strategy-parameters must be a JSON object")
        resolver = FeatureDependencyResolverService(
            FeatureDatasetVersionsRepository(session),
            registry=get_registry(),
        )
        try:
            result = resolver.resolve(
                strategy_type=args.strategy_type,
                strategy_parameters=strategy_parameters,
                simulation_dataset_version=args.dataset_version_id,
                price_basis=PriceBasis(args.price_basis),
                symbols=_parse_symbols(args.symbols),
                start_date=_parse_date(args.start_date),
                end_date=_parse_date(args.end_date),
            )
            payload = {
                "ok": True,
                "strategy_type": args.strategy_type,
                "warmup_bars": result.warmup_bars,
                "resolved_feature_dataset_ids": result.resolved_feature_dataset_ids,
                "feature_requests": [
                    {
                        "feature_name": req.feature_name,
                        "dataset_version": req.dataset_version,
                    }
                    for req in result.feature_requests
                ],
            }
            rc = 0
        except FeatureDependencyError as exc:
            payload = {"ok": False, "strategy_type": args.strategy_type, "error": str(exc)}
            rc = 1
        _print_payload("Feature Dependencies", payload, args.format)
        return rc
    finally:
        session.close()


def handle_inspect_cache(args: argparse.Namespace) -> int:
    cache, path = _cache_for_name(args.cache, args.cache_store)
    entries = cache.entries()
    payload = {
        "cache": args.cache,
        "cache_store": str(path),
        "stats": cache.stats(),
        "entry_count": len(entries),
        "entries_preview": entries[:25],
    }
    _print_payload("Research Cache", payload, args.format)
    return 0


def handle_validate_cache(args: argparse.Namespace) -> int:
    cache, path = _cache_for_name(args.cache, args.cache_store)
    entries = cache.entries()
    problems = _cache_entry_problems(args.cache, entries)
    payload = {
        "cache": args.cache,
        "cache_store": str(path),
        "ok": not problems,
        "entry_count": len(entries),
        "problems": problems,
    }
    _print_payload("Research Cache Validation", payload, args.format)
    return 0 if not problems else 1


def handle_clear_cache(args: argparse.Namespace) -> int:
    cache, path = _cache_for_name(args.cache, args.cache_store)
    entries = cache.entries()
    execute = bool(args.execute)
    if execute:
        cache.clear()
    payload = {
        "cache": args.cache,
        "cache_store": str(path),
        "dry_run": not execute,
        "removed_count": len(entries) if execute else 0,
        "would_remove_count": len(entries),
    }
    _print_payload("Research Cache Clear", payload, args.format)
    return 0


def handle_run_validation(args: argparse.Namespace) -> int:
    config = _load_artifact(args.config)
    payload = {
        "artifact_type": "research_validation_plan",
        "validation_type": args.type,
        "config": config,
        "status": "planned",
        "message": "Validation config parsed; execution is intentionally explicit.",
    }
    _emit_artifact_if_requested(payload=payload, output=args.output, output_format=args.format)
    _print_payload("Research Validation Plan", payload, args.format)
    return 0


def handle_analyze_regimes(args: argparse.Namespace) -> int:
    payload = _experiment_scoped_artifact(
        experiment_id=args.experiment_id,
        artifact_type="regime_analysis_request",
    )
    _emit_artifact_if_requested(payload=payload, output=args.output, output_format=args.format)
    _print_payload("Regime Analysis Request", payload, args.format)
    return 0


def handle_intelligence_rank_candidates(args: argparse.Namespace) -> int:
    payload = _experiment_intelligence_payload(
        experiment_id=args.experiment_id,
        operation="rank_candidates",
    )
    _emit_artifact_if_requested(payload=payload, output=args.output, output_format=args.format)
    _print_payload("Research Intelligence Ranking", payload, args.format)
    return 0


def handle_intelligence_cluster_strategies(args: argparse.Namespace) -> int:
    payload = _experiment_intelligence_payload(
        experiment_id=args.experiment_id,
        operation="cluster_strategies",
    )
    _emit_artifact_if_requested(payload=payload, output=args.output, output_format=args.format)
    _print_payload("Research Intelligence Clustering", payload, args.format)
    return 0


def handle_intelligence_predict_robustness(args: argparse.Namespace) -> int:
    payload = _experiment_intelligence_payload(
        experiment_id=args.experiment_id,
        operation="predict_robustness",
    )
    _emit_artifact_if_requested(payload=payload, output=args.output, output_format=args.format)
    _print_payload("Research Intelligence Robustness", payload, args.format)
    return 0


def handle_calibrate_slippage(args: argparse.Namespace) -> int:
    source = Path(args.fills_source)
    if not source.exists():
        raise ValueError(f"--fills-source does not exist: {source}")
    payload = {
        "artifact_type": "slippage_calibration_request",
        "fills_source": str(source),
        "source_exists": True,
        "status": "planned",
        "message": "Calibration source validated; use calibration service for execution.",
    }
    _write_artifact(args.output, payload, args.format)
    _print_payload("Slippage Calibration Request", payload, args.format)
    return 0


def handle_inspect_cost_model(args: argparse.Namespace) -> int:
    raw = _load_artifact(args.config) if args.config else {}
    config_raw = raw.get("cost_model", raw)
    commission_per_share = str(config_raw.get("commission_per_share", "0.0000"))
    min_commission = str(config_raw.get("min_commission", "0.00"))
    config = SimulationCostModelConfig(
        commission_per_share=Decimal(commission_per_share),
        min_commission=Decimal(min_commission),
    )
    payload = {
        "commission_per_share": str(config.commission_per_share),
        "min_commission": str(config.min_commission),
        "slippage_model": config_raw.get("slippage_model", "fixed_bps"),
        "slippage_config": config_raw.get("slippage_config", {}),
    }
    _print_payload("Simulation Cost Model", payload, args.format)
    return 0


def _plan_from_args(args: argparse.Namespace, simulation_context) -> ExperimentDefinition:
    if args.config:
        return _load_experiment_from_yaml(args.config, simulation_context, args=args)

    missing = [
        flag
        for flag, value in [
            ("--experiment-id", args.experiment_id),
            ("--dataset-version-id", args.dataset_version_id),
            ("--price-basis", args.price_basis),
            ("--symbols", args.symbols),
            ("--start-date", args.start_date),
            ("--end-date", args.end_date),
            ("--strategy-type", args.strategy_type),
            ("--random-seed or --base-seed", args.random_seed or args.base_seed),
        ]
        if value is None
    ]
    if missing:
        raise SystemExit(
            "error: the following arguments are required when --config is not supplied: "
            + ", ".join(missing)
        )

    strategy_parameters = json.loads(args.strategy_parameters)
    if not isinstance(strategy_parameters, dict):
        raise ValueError("--strategy-parameters must be a JSON object")
    parameter_space = json.loads(args.parameter_space) if args.parameter_space else None
    if parameter_space is not None and not isinstance(parameter_space, dict):
        raise ValueError("--parameter-space must be a JSON object")

    return ExperimentDefinition(
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
        random_seed=args.base_seed if args.base_seed is not None else args.random_seed,
        initial_cash=args.initial_cash,
    )


def _experiment_plan_payload(plan: ExperimentDefinition) -> dict[str, Any]:
    return {
        "experiment_id": plan.experiment_id,
        "experiment_type": plan.experiment_type.value,
        "description": plan.description,
        "dataset_version": plan.dataset_version,
        "universe_version": plan.universe_version,
        "price_basis": plan.price_basis.value,
        "symbols": plan.symbols,
        "symbol_count": len(plan.symbols),
        "start_date": plan.start_date.isoformat(),
        "end_date": plan.end_date.isoformat(),
        "random_seed": plan.random_seed,
        "initial_cash": plan.initial_cash,
        "strategy_count": len(plan.strategy_set),
        "parameter_grid_count": len(plan.parameter_grid),
        "parameter_space_keys": sorted((plan.parameter_space or {}).keys()),
        "has_staged_pipeline": plan.staged_pipeline_config is not None,
        "stage_count": len(plan.staged_pipeline_config.stages)
        if plan.staged_pipeline_config is not None
        else 0,
    }


def _experiment_work_units(plan: ExperimentDefinition) -> list[dict[str, Any]]:
    if plan.staged_pipeline_config is not None:
        return [
            {
                "unit_type": "stage",
                "stage_index": index,
                "stage_name": getattr(stage, "stage_name", stage.__class__.__name__),
            }
            for index, stage in enumerate(plan.staged_pipeline_config.stages, start=1)
        ]

    parameter_space = plan.parameter_space or {}
    run_count = 1
    for values in parameter_space.values():
        if isinstance(values, list):
            run_count *= max(len(values), 1)
    run_count = max(run_count, len(plan.strategy_set), 1)
    return [
        {
            "unit_type": "simulation",
            "ordinal": index,
            "experiment_id": plan.experiment_id,
        }
        for index in range(1, run_count + 1)
    ]


def _simulation_run_payload(run: SimulationRuns, *, include_config: bool) -> dict[str, Any]:
    payload = {
        "run_id": run.run_id,
        "experiment_id": run.experiment_id,
        "strategy_id": run.strategy_id,
        "dataset_version": run.dataset_version,
        "universe_version": run.universe_version,
        "price_basis": run.price_basis,
        "symbols": run.symbols,
        "symbol_count": len(run.symbols),
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "window_role": run.window_role,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "end_time": run.end_time.isoformat() if run.end_time else None,
        "status": run.status,
        "metrics_snapshot_id": run.metrics_snapshot_id,
    }
    if include_config:
        payload["execution_config"] = run.execution_config
    return payload


def _metrics_payload(metrics: MetricsSummary | None) -> dict[str, Any] | None:
    if metrics is None:
        return None
    return {
        "metrics_snapshot_id": metrics.metrics_snapshot_id,
        "created_at": metrics.created_at.isoformat() if metrics.created_at else None,
        "total_return": metrics.total_return,
        "sharpe_ratio": metrics.sharpe_ratio,
        "max_drawdown": metrics.max_drawdown,
        "trade_count": metrics.trade_count,
        "winning_trade_count": metrics.winning_trade_count,
        "losing_trade_count": metrics.losing_trade_count,
        "volatility": metrics.volatility,
        "metrics_json": metrics.metrics_json,
        "metric_lineage_type": metrics.metric_lineage_type,
        "environment": metrics.environment,
        "calculation_version": metrics.calculation_version,
    }


def _cache_entry_problems(cache_name: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = (
        {"config_hash", "strategy_type", "first_seen_at"}
        if cache_name == "strategy-generation"
        else {"key", "key_id", "run_id", "cached_at"}
    )
    problems: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        missing = sorted(required - set(entry))
        if missing:
            problems.append({"index": index, "missing": missing})
    return problems


def _experiment_scoped_artifact(*, experiment_id: str, artifact_type: str) -> dict[str, Any]:
    session = get_session()
    try:
        svc = ExperimentCatalogService(session=session)
        try:
            experiment = svc.get_experiment_detail(experiment_id=experiment_id)
        except LookupError:
            experiment = {"experiment_id": experiment_id, "found": False}
        return {
            "artifact_type": artifact_type,
            "experiment_id": experiment_id,
            "experiment": experiment,
            "created_at": datetime.now(UTC).isoformat(),
        }
    finally:
        session.close()


def _experiment_intelligence_payload(*, experiment_id: str, operation: str) -> dict[str, Any]:
    artifact = _experiment_scoped_artifact(
        experiment_id=experiment_id,
        artifact_type=f"research_intelligence_{operation}",
    )
    strategies = artifact.get("experiment", {}).get("strategies", [])
    if isinstance(strategies, list):
        artifact["candidate_count"] = len(strategies)
        artifact["candidates_preview"] = strategies[:25]
    artifact["operation"] = operation
    artifact["status"] = "planned"
    return artifact


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
    parser.add_argument("--initial-cash", type=_positive_float, default=100_000.0)
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", choices=["json", "yaml"], default="json")
    parser.set_defaults(func=handle_run_simulation)


# ---------------------------------------------------------------------------
# black-litterman
# ---------------------------------------------------------------------------


def _register_black_litterman(subparsers) -> None:
    parser = subparsers.add_parser(
        "black-litterman",
        help="Research-only Black-Litterman allocation prototype",
    )
    bl_subparsers = parser.add_subparsers(dest="black_litterman_command", required=True)

    run_parser = bl_subparsers.add_parser(
        "run",
        help="Run a research-only Black-Litterman analysis from YAML/JSON config",
    )
    run_parser.add_argument("--config", required=True, help="Path to YAML/JSON views config")
    run_parser.add_argument(
        "--format",
        choices=["json", "yaml", "text"],
        default="json",
        help="Output format",
    )
    run_parser.set_defaults(func=handle_black_litterman_run)


def handle_black_litterman_run(args: argparse.Namespace) -> int:
    config = _load_black_litterman_config(args.config)
    with get_session() as session:
        artifact = BlackLittermanResearchService(session=session).run(config)
        _print_payload(
            "Black-Litterman Research Run",
            artifact.model_dump(mode="json"),
            args.format,
        )
    return 0


def _register_inspect_checkpoints(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-checkpoints",
        help="Inspect research checkpoint store entries",
    )
    parser.add_argument("--checkpoint-store", required=True)
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.set_defaults(func=handle_inspect_checkpoints)


def _register_plan_restart(subparsers) -> None:
    parser = subparsers.add_parser(
        "plan-restart",
        help="Build a dry-run research restart plan from expected unit identities",
    )
    parser.add_argument("--checkpoint-store", required=True)
    parser.add_argument(
        "--units-file",
        required=True,
        help="JSON/YAML file containing a list of ResearchCheckpointIdentity mappings",
    )
    parser.add_argument("--resume-failed-only", action="store_true")
    parser.add_argument("--resume-missing-only", action="store_true")
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.set_defaults(func=handle_plan_restart)


def _register_resume_experiment(subparsers) -> None:
    parser = subparsers.add_parser(
        "resume-experiment",
        help="Research-only resume planner; use --execute from pipeline code paths",
    )
    parser.add_argument("--checkpoint-store", required=True)
    parser.add_argument("--units-file", required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Rejected by this deprecated alias; use pipeline execution paths instead",
    )
    parser.add_argument("--resume-failed-only", action="store_true")
    parser.add_argument("--resume-missing-only", action="store_true")
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument("--format", choices=["json", "yaml", "table"], default="json")
    parser.set_defaults(func=handle_resume_experiment)


def _load_checkpoint_units(path: str) -> list[ResearchCheckpointIdentity]:
    payload = _load_artifact(path)
    raw_units = payload.get("units", payload.get("expected_units"))
    if raw_units is None and isinstance(payload.get("identity"), dict):
        raw_units = [payload["identity"]]
    if not isinstance(raw_units, list):
        raise ValueError("--units-file must contain a 'units' list")
    return [ResearchCheckpointIdentity.from_dict(item) for item in raw_units]


def handle_inspect_checkpoints(args: argparse.Namespace) -> int:
    service = ResearchCheckpointService(persist_path=args.checkpoint_store)
    checkpoints = [checkpoint.to_dict() for checkpoint in service.list_checkpoints()]
    _print_payload(
        "Research Checkpoints",
        {"count": len(checkpoints), "checkpoints": checkpoints},
        args.format,
    )
    return 0


def handle_plan_restart(args: argparse.Namespace) -> int:
    if args.resume_failed_only and args.resume_missing_only:
        raise ValueError("Choose at most one of --resume-failed-only and --resume-missing-only")
    service = ResearchCheckpointService(persist_path=args.checkpoint_store)
    plan = service.plan_restart(
        _load_checkpoint_units(args.units_file),
        dry_run=True,
        resume_failed=not args.resume_missing_only,
        resume_missing=not args.resume_failed_only,
        force_rerun=args.force_rerun,
    )
    _print_payload("Research Restart Plan", plan.to_dict(), args.format)
    return 0


def handle_resume_experiment(args: argparse.Namespace) -> int:
    # This CLI command intentionally plans only. Execution happens inside
    # research pipeline stages so live/paper runtime orchestration is untouched.
    if getattr(args, "execute", False):
        raise SystemExit(
            "error: research resume-experiment is a deprecated planning alias; "
            "use plan-restart or pipeline execution paths for guarded execution"
        )
    return handle_plan_restart(args)


def handle_run_simulation(args: argparse.Namespace) -> int:
    """Handle the run-simulation subcommand.

    Two execution paths:
      - With --experiment-id: routes through ExperimentOrchestrationService.
        Persists the experiment and run to the DB. Treats the single strategy
        as an AB experiment so the orchestrator accepts it.
      - Without --experiment-id: calls SimulationRunner directly. Nothing is
        persisted. Fastest path for iterating on a strategy or data issue.
    """
    strategy_parameters = json.loads(args.strategy_parameters)

    if not isinstance(strategy_parameters, dict):
        raise ValueError("--strategy-parameters must be a JSON object")

    if getattr(args, "dry_run", False):
        payload = {
            "dry_run": True,
            "execution_path": "experiment_orchestration"
            if args.experiment_id
            else "direct_simulation",
            "experiment_id": args.experiment_id,
            "strategy_id": args.strategy_id,
            "strategy_type": args.strategy_type,
            "strategy_parameters": strategy_parameters,
            "dataset_version": args.dataset_version_id,
            "price_basis": args.price_basis,
            "symbols": _parse_symbols(args.symbols),
            "start_date": args.start_date,
            "end_date": args.end_date,
            "random_seed": args.random_seed,
            "initial_cash": args.initial_cash,
            "strict_data_loading": args.strict_data_loading,
            "shuffle_timestamps": args.shuffle_timestamps,
            "will_persist_experiment_state": bool(args.experiment_id),
        }
        _emit_artifact_if_requested(
            payload=payload,
            output=getattr(args, "output", None),
            output_format=getattr(args, "format", "json"),
        )
        print_header("Run Simulation Plan")
        print_json(payload)
        return 0

    session = get_session()

    try:
        simulation_context = build_simulation_context(session=session)

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
        payload = {
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
        _emit_artifact_if_requested(
            payload=payload,
            output=getattr(args, "output", None),
            output_format=getattr(args, "format", "json"),
        )
        print_json(payload)

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
    parser.add_argument("--initial-cash", type=_positive_float, default=100_000.0)
    parser.add_argument(
        "--execution-mode",
        choices=["serial", "parallel"],
        default="serial",
        help="Research pipeline execution mode for independent staged units.",
    )
    parser.add_argument("--max-workers", type=_positive_int, default=1)
    parser.add_argument(
        "--base-seed",
        type=int,
        default=None,
        help="Override experiment random_seed for deterministic per-unit seed derivation.",
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", choices=["json", "yaml"], default="json")
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
            plan = _load_experiment_from_yaml(args.config, simulation_context, args=args)
        else:
            strategy_parameters = json.loads(args.strategy_parameters)
            parameter_space = json.loads(args.parameter_space) if args.parameter_space else None
            random_seed = args.base_seed if args.base_seed is not None else args.random_seed

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
                random_seed=random_seed,
                initial_cash=args.initial_cash,
            )

        if getattr(args, "dry_run", False):
            payload = {
                "dry_run": True,
                "plan": _experiment_plan_payload(plan),
                "work_units": _experiment_work_units(plan),
            }
            _emit_artifact_if_requested(
                payload=payload,
                output=getattr(args, "output", None),
                output_format=getattr(args, "format", "json"),
            )
            print_header("Research Experiment Plan")
            print_json(payload)
            return 0

        if plan.staged_pipeline_config is not None:
            pipeline_result = (
                simulation_context.experiment_orchestration_service.run_staged_experiment(plan)
            )

            print_header(f"Experiment — {plan.experiment_id} (staged pipeline)")
            payload = {
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
            _emit_artifact_if_requested(
                payload=payload,
                output=getattr(args, "output", None),
                output_format=getattr(args, "format", "json"),
            )
            print_json(payload)
        else:
            results, filter_outputs = (
                simulation_context.experiment_orchestration_service.run_experiment(plan)
            )
            print_header(f"Experiment — {plan.experiment_id}")
            payload = {
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
            _emit_artifact_if_requested(
                payload=payload,
                output=getattr(args, "output", None),
                output_format=getattr(args, "format", "json"),
            )
            print_json(payload)

        return 0

    finally:
        session.close()


def _load_experiment_from_yaml(
    config_path: str,
    simulation_context,
    args: argparse.Namespace | None = None,
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
        stage_raws = list(pipeline_raw["stages"])
        if args is not None:
            stage_raws = [
                {
                    **stage_raw,
                    "execution_mode": stage_raw.get("execution_mode", args.execution_mode),
                    "max_workers": stage_raw.get("max_workers", args.max_workers),
                    "fail_fast": stage_raw.get("fail_fast", args.fail_fast),
                }
                for stage_raw in stage_raws
            ]
        stages = [
            StageRegistry.load(stage_raw, simulation_context.simulation_runner)
            for stage_raw in stage_raws
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
        "start_date": raw.get("start_date")
        or (
            min(
                (s["start_date"] for s in pipeline_raw["stages"] if s.get("start_date")),
                default=None,
            )
            if pipeline_raw
            else None
        ),
        "end_date": raw.get("end_date")
        or (
            max(
                (s["end_date"] for s in pipeline_raw["stages"] if s.get("end_date")),
                default=None,
            )
            if pipeline_raw
            else None
        ),
        "random_seed": args.base_seed
        if args and args.base_seed is not None
        else raw.get("random_seed", 42),
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
        type=_positive_int,
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
    parser.add_argument("--population-size", type=_positive_int, default=20)
    parser.add_argument("--generations", type=_positive_int, default=3)
    parser.add_argument("--mutation-rate", type=_pct, default=0.25)
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
