from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_strategies_service import (
    ActiveStrategiesService,
)
from autonomous_trading_platform.application.services.strategy_catalog_service import (
    StrategyCatalogService,
)
from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
    check_ingestion_readiness_job,
)
from autonomous_trading_platform.strategy.catalog import list_strategy_types
from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.registry import get_registry

_STRATEGY_TYPE_CHOICES = list_strategy_types()


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


@dataclass
class StrategyCatalogDependencies:
    session: Session


def build_catalog_dependencies() -> StrategyCatalogDependencies:
    return StrategyCatalogDependencies(session=get_session())


def _is_missing_table(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "no such table" in msg or "does not exist" in msg or "relation" in msg


def _db_not_ready(exc: Exception) -> int:
    if _is_missing_table(exc):
        print(
            "[strategy] DB tables not found. "
            "Run: docker compose up -d && alembic -c infra/db/alembic.ini upgrade head"
        )
    else:
        print(f"[strategy] DB error: {exc}")
    return 1


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    strategy_parser = subparsers.add_parser("strategy", help="Strategy operations")
    strategy_subparsers = strategy_parser.add_subparsers(
        dest="strategy_command",
        required=True,
    )

    _register_evaluate_bar(strategy_subparsers)
    _register_inspect_readiness(strategy_subparsers)
    _register_list_types(strategy_subparsers)
    _register_inspect_type(strategy_subparsers)
    _register_validate_config(strategy_subparsers)
    _register_list(strategy_subparsers)
    _register_inspect(strategy_subparsers)
    _register_compare(strategy_subparsers)
    _register_equity_curve(strategy_subparsers)
    _register_list_components(strategy_subparsers)
    _register_inspect_component(strategy_subparsers)
    _register_active(strategy_subparsers)


# ---------------------------------------------------------------------------
# evaluate-bar
# ---------------------------------------------------------------------------


def _register_evaluate_bar(subparsers) -> None:
    parser = subparsers.add_parser(
        "evaluate-bar",
        help=(
            "[DEPRECATED] Use 'runtime evaluate-cycle --timestamp ...' instead. "
            "Forwards to the full trading evaluation cycle."
        ),
    )
    parser.add_argument("--timestamp", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print execution plan without running the cycle or touching broker APIs",
    )
    parser.set_defaults(func=handle_evaluate_bar)


def handle_evaluate_bar(args: argparse.Namespace) -> int:
    print(
        "[strategy evaluate-bar] DEPRECATED: use 'runtime evaluate-cycle --timestamp "
        f"{args.timestamp}' instead. Forwarding."
    )
    from autonomous_trading_platform.cli.commands import runtime as _runtime

    return _runtime.handle_evaluate_cycle(args)


# ---------------------------------------------------------------------------
# inspect-readiness
# ---------------------------------------------------------------------------


def _register_inspect_readiness(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-readiness",
        help=(
            "[DOMAIN-MISALIGNED] Check ingestion deadline readiness for the current cycle. "
            "This command belongs in 'operations' or 'diagnostics'; it is retained here "
            "for backwards compatibility."
        ),
    )
    parser.add_argument("--timestamp")
    parser.set_defaults(func=handle_inspect_readiness)


def handle_inspect_readiness(args: argparse.Namespace) -> int:
    timestamp = parse_datetime(args.timestamp) if args.timestamp else None
    result = check_ingestion_readiness_job(
        run_id=str(uuid4()),
        now_utc=timestamp,
    )
    print_header("Ingestion Readiness")
    print_json(
        {
            "timestamp": args.timestamp,
            "ingestion_ready": result.ready,
            "safe_mode": result.safe_mode,
            "reason": result.reason,
            "domain_note": (
                "This command checks ingestion deadline readiness, not strategy readiness. "
                "It should be moved to 'operations inspect-ingestion-readiness' or "
                "'diagnostics ingestion-readiness'."
            ),
        }
    )
    return 0


# ---------------------------------------------------------------------------
# list-types  (registry — no DB)
# ---------------------------------------------------------------------------


def _register_list_types(subparsers) -> None:
    parser = subparsers.add_parser(
        "list-types",
        help="List registered strategy types with family, production/debug flags, warmup, and indicators",
    )
    parser.add_argument(
        "--family",
        choices=[f.value for f in get_registry().list_families()],
        help="Filter by strategy family",
    )
    parser.add_argument("--include-debug", action="store_true")
    parser.add_argument("--include-experimental", action="store_true")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_list_types)


def handle_list_types(args: argparse.Namespace) -> int:
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
                "required_persisted_features": list(defn.required_persisted_features),
            }
        )
    print_header("Strategy Types")
    print_json({"count": len(rows), "strategy_types": rows})
    return 0


# ---------------------------------------------------------------------------
# inspect-type  (registry — no DB)
# ---------------------------------------------------------------------------


def _register_inspect_type(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-type",
        help="Inspect one strategy type: default parameters, schema, compatibility, warmup, indicators",
    )
    parser.add_argument("--strategy-type", required=True, choices=_STRATEGY_TYPE_CHOICES)
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_inspect_type)


def _strategy_definition_to_dict(defn) -> dict:
    registry = get_registry()
    defaults = registry.get_default_parameters(defn.strategy_type)
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
        "parameter_specs": [_parameter_spec_to_dict(s) for s in defn.parameter_specs],
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


def _enum_val(v: object) -> object:
    return v.value if hasattr(v, "value") else v


def _parameter_spec_to_dict(spec) -> dict:
    return {
        "name": spec.name,
        "type": _enum_val(spec.parameter_type),
        "default": spec.default,
        "description": spec.description,
        "min_value": spec.min_value,
        "max_value": spec.max_value,
        "discrete": spec.discrete,
        "step": spec.step,
        "tunable": spec.tunable,
        "mutation_strategy": spec.mutation_strategy,
    }


def handle_inspect_type(args: argparse.Namespace) -> int:
    defn = get_registry().get_definition(args.strategy_type)
    print_header("Strategy Type")
    print_json(_strategy_definition_to_dict(defn))
    return 0


# ---------------------------------------------------------------------------
# validate-config  (registry — no DB)
# ---------------------------------------------------------------------------


def _register_validate_config(subparsers) -> None:
    parser = subparsers.add_parser(
        "validate-config",
        help="Validate and normalize parameters for a strategy type",
        epilog=(
            "PowerShell note: use escaped double quotes or a file.\n"
            "  --parameters '{\\\"lookback\\\":20}'          (escaped inline)\n"
            "  --parameters-file params.json               (preferred on Windows)\n"
            "  $p = '{\"lookback\":20}'; ... --parameters $p  (via variable)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--strategy-type", required=True, choices=_STRATEGY_TYPE_CHOICES)
    param_group = parser.add_mutually_exclusive_group()
    param_group.add_argument(
        "--parameters",
        default=None,
        help=(
            "JSON object of parameters, e.g. '{\"lookback\":20}'. "
            "On PowerShell use escaped quotes: '{\\\"lookback\\\":20}' "
            "or prefer --parameters-file."
        ),
    )
    param_group.add_argument(
        "--parameters-file",
        default=None,
        help="Path to a JSON file containing the parameters object (avoids shell quoting issues)",
    )
    parser.set_defaults(func=handle_validate_config)


def _load_validate_config_params(args: argparse.Namespace) -> tuple[dict, str | None]:
    """Return (params_dict, error_string). error_string is None on success."""
    from pathlib import Path

    if args.parameters_file:
        try:
            raw = Path(args.parameters_file).read_text(encoding="utf-8")
        except OSError as exc:
            return {}, f"Cannot read --parameters-file: {exc}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {}, f"Invalid JSON in --parameters-file: {exc}"
        if not isinstance(parsed, dict):
            return {}, "--parameters-file must contain a JSON object"
        return parsed, None

    raw_str = args.parameters if args.parameters is not None else "{}"
    try:
        parsed = json.loads(raw_str)
    except json.JSONDecodeError as exc:
        return {}, (
            f"Invalid JSON: {exc}. "
            "On PowerShell, use escaped quotes: '{\\\"lookback\\\":20}' "
            "or pass parameters via --parameters-file path/to/params.json"
        )
    if not isinstance(parsed, dict):
        return {}, "--parameters must be a JSON object"
    return parsed, None


def handle_validate_config(args: argparse.Namespace) -> int:
    registry = get_registry()
    raw_params, err = _load_validate_config_params(args)
    if err is not None:
        print_header("Validate Config")
        print_json({"valid": False, "error": err})
        return 1

    try:
        registry.validate_parameters(args.strategy_type, raw_params)
        normalized = registry.normalize_parameters(args.strategy_type, raw_params)
        print_header("Validate Config")
        print_json(
            {
                "valid": True,
                "strategy_type": args.strategy_type,
                "input_parameters": raw_params,
                "normalized_parameters": normalized,
            }
        )
        return 0
    except Exception as exc:
        print_header("Validate Config")
        print_json(
            {
                "valid": False,
                "strategy_type": args.strategy_type,
                "input_parameters": raw_params,
                "error": str(exc),
            }
        )
        return 1


# ---------------------------------------------------------------------------
# list  (DB-backed)
# ---------------------------------------------------------------------------


def _register_list(subparsers) -> None:
    parser = subparsers.add_parser(
        "list",
        help="List persisted strategies with status and key metrics",
    )
    parser.add_argument(
        "--status",
        choices=["live", "paper", "research", "off"],
        help="Filter by strategy status",
    )
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_list)


def handle_list(args: argparse.Namespace) -> int:
    try:
        deps = build_catalog_dependencies()
        service = StrategyCatalogService(session=deps.session)
        strategies = service.list_strategies(status_filter=getattr(args, "status", None))
        print_header("Strategy List")
        print_json({"count": len(strategies), "strategies": strategies})
        return 0
    except Exception as exc:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        if isinstance(exc, (OperationalError, ProgrammingError)):
            return _db_not_ready(exc)
        raise


# ---------------------------------------------------------------------------
# inspect  (DB-backed)
# ---------------------------------------------------------------------------


def _register_inspect(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect",
        help="Show persisted strategy config, metrics, governance status, and deployment history",
    )
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_inspect)


def handle_inspect(args: argparse.Namespace) -> int:
    try:
        deps = build_catalog_dependencies()
        service = StrategyCatalogService(session=deps.session)
        detail = service.get_strategy_detail(strategy_id=args.strategy_id)
        print_header("Strategy Detail")
        print_json(detail)
        return 0
    except LookupError as exc:
        print_header("Strategy Detail")
        print_json({"error": str(exc), "strategy_id": args.strategy_id})
        return 1
    except Exception as exc:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        if isinstance(exc, (OperationalError, ProgrammingError)):
            return _db_not_ready(exc)
        raise


# ---------------------------------------------------------------------------
# compare  (DB-backed)
# ---------------------------------------------------------------------------


def _register_compare(subparsers) -> None:
    parser = subparsers.add_parser(
        "compare",
        help="Compare metrics for selected persisted strategies",
    )
    parser.add_argument(
        "--strategy-ids",
        required=True,
        help="Comma-separated strategy IDs, e.g. strat_a,strat_b",
    )
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_compare)


def handle_compare(args: argparse.Namespace) -> int:
    strategy_ids = [s.strip() for s in args.strategy_ids.split(",") if s.strip()]
    if len(strategy_ids) < 2:
        print_header("Strategy Compare")
        print_json({"error": "--strategy-ids requires at least two strategy IDs"})
        return 1

    try:
        deps = build_catalog_dependencies()
        service = StrategyCatalogService(session=deps.session)
        result = service.compare_strategies(strategy_ids=strategy_ids)
        print_header("Strategy Compare")
        print_json(result)
        return 0
    except LookupError as exc:
        print_header("Strategy Compare")
        print_json({"error": str(exc)})
        return 1
    except Exception as exc:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        if isinstance(exc, (OperationalError, ProgrammingError)):
            return _db_not_ready(exc)
        raise


# ---------------------------------------------------------------------------
# equity-curve  (DB-backed)
# ---------------------------------------------------------------------------


def _register_equity_curve(subparsers) -> None:
    parser = subparsers.add_parser(
        "equity-curve",
        help="Read latest equity curve for one strategy",
    )
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_equity_curve)


def handle_equity_curve(args: argparse.Namespace) -> int:
    try:
        deps = build_catalog_dependencies()
        service = StrategyCatalogService(session=deps.session)
        result = service.get_strategy_equity_curve(strategy_id=args.strategy_id)
        print_header("Strategy Equity Curve")
        print_json(result)
        return 0
    except LookupError as exc:
        print_header("Strategy Equity Curve")
        print_json({"error": str(exc), "strategy_id": args.strategy_id})
        return 1
    except Exception as exc:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        if isinstance(exc, (OperationalError, ProgrammingError)):
            return _db_not_ready(exc)
        raise


# ---------------------------------------------------------------------------
# list-components  (component registry — no DB)
# ---------------------------------------------------------------------------


def _register_list_components(subparsers) -> None:
    parser = subparsers.add_parser(
        "list-components",
        help="List registered strategy components, indicators, and rules",
    )
    parser.add_argument("--component-type", choices=[t.value for t in ComponentType])
    status = parser.add_mutually_exclusive_group()
    status.add_argument("--executable-only", action="store_true")
    status.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_list_components)


def _component_summary(defn) -> dict:
    return {
        "component_name": defn.component_name,
        "component_type": defn.component_type.value,
        "display_name": defn.display_name,
        "is_executable": defn.is_executable,
        "metadata_only": defn.metadata_only,
        "parameter_count": len(defn.parameter_specs),
        "required_inputs": list(defn.required_inputs),
        "warmup": defn.warmup_formula or defn.warmup_bars,
    }


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
        rows.append(_component_summary(defn))
    print_header("Strategy Components")
    print_json({"count": len(rows), "components": rows})
    return 0


# ---------------------------------------------------------------------------
# inspect-component  (component registry — no DB)
# ---------------------------------------------------------------------------


def _register_inspect_component(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect-component",
        help="Inspect one component: metadata, inputs, parameters, compatibility, warmup",
    )
    parser.add_argument("--component-name", required=True)
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_inspect_component)


def _component_definition_to_dict(defn) -> dict:
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
        "parameters": [_parameter_spec_to_dict(s) for s in defn.parameter_specs],
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
            "compatible_component_types": [_enum_val(t) for t in defn.compatible_component_types],
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


def handle_inspect_component(args: argparse.Namespace) -> int:
    try:
        defn = get_component_registry().get_component_definition(args.component_name)
    except (KeyError, LookupError) as exc:
        print_header("Component Detail")
        print_json({"error": str(exc), "component_name": args.component_name})
        return 1
    print_header("Component Detail")
    print_json(_component_definition_to_dict(defn))
    return 0


# ---------------------------------------------------------------------------
# active  (DB-backed)
# ---------------------------------------------------------------------------


def _register_active(subparsers) -> None:
    parser = subparsers.add_parser(
        "active",
        help="List active paper/live strategies",
    )
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.set_defaults(func=handle_active)


def handle_active(args: argparse.Namespace) -> int:
    try:
        deps = build_catalog_dependencies()
        service = ActiveStrategiesService(session=deps.session)
        strategies = service.list_active_strategies()
        print_header("Active Strategies")
        print_json({"count": len(strategies), "strategies": strategies})
        return 0
    except Exception as exc:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        if isinstance(exc, (OperationalError, ProgrammingError)):
            return _db_not_ready(exc)
        raise
