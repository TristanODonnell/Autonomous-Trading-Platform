from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select

from autonomous_trading_platform.application.services.feature_dataset_command_service import (
    FeatureDatasetCommandService,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.observability.telemetry import setup_telemetry
from autonomous_trading_platform.runtime.services.feature_dataset_validation_service import (
    FeatureDatasetValidationService,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    MixedLineageError,
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.storage.parquet import paths as parquet_paths
from autonomous_trading_platform.storage.parquet.repositories.parquet_feature_repository import (
    FEATURE_DATASETS_BY_NAME,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.feature_dataset_versions_repository import (
    FeatureDatasetVersionsRepository,
)

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_FEATURES_BY_STRATEGY: dict[str, list[str]] = {
    "momentum": ["returns", "volatility", "moving_average"],
    "regime": ["regime", "regime_classification"],
    "liquidity": ["liquidity"],
    "all": sorted(FEATURE_DATASETS_BY_NAME),
}


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, PriceBasis):
        return value.value
    return str(value)


def _emit(payload: dict[str, Any], output: str | None = None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=_json_default)
    print(rendered)
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")


def _parse_price_basis(value: str | PriceBasis) -> PriceBasis:
    if isinstance(value, PriceBasis):
        return value
    return PriceBasis(value.lower())


def _parse_symbols(value: str | None, *, required: bool = False) -> list[str] | None:
    if value is None or value.strip() == "":
        if required:
            raise ValueError("--symbols is required")
        return None

    symbols: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    invalid: list[str] = []
    for raw_symbol in value.split(","):
        symbol = raw_symbol.strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_RE.match(symbol):
            invalid.append(symbol)
            continue
        if symbol in seen:
            duplicates.append(symbol)
            continue
        seen.add(symbol)
        symbols.append(symbol)

    if invalid:
        raise ValueError(f"Invalid symbols: {', '.join(invalid)}")
    if duplicates:
        raise ValueError(f"Duplicate symbols: {', '.join(duplicates)}")
    if required and not symbols:
        raise ValueError("--symbols must contain at least one symbol")
    return symbols or None


def _validate_date_window(start_date: date | None, end_date: date | None) -> None:
    if start_date is None or end_date is None:
        raise ValueError("--start-date and --end-date are required")
    if end_date < start_date:
        raise ValueError("--end-date must be greater than or equal to --start-date")


def _load_json_object(raw: str | None, *, option_name: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{option_name} must decode to a JSON object")
    return cast(dict[str, Any], parsed)


def _feature_row_to_dict(row: FeatureDatasetVersions) -> dict[str, Any]:
    return {
        "feature_dataset_version_id": row.dataset_version_id,
        "feature_name": row.feature_name,
        "dataset_name": row.dataset_name,
        "created_at": row.created_at,
        "schema_version": row.schema_version,
        "source_dataset_version": row.source_dataset_version,
        "underlying_price_basis": row.underlying_price_basis,
        "computation_parameters": row.computation_parameters,
        "computation_code_version": row.computation_code_version,
        "storage_path": row.storage_path,
        "symbol_coverage": row.symbol_coverage,
        "date_coverage_start": row.date_coverage_start,
        "date_coverage_end": row.date_coverage_end,
        "validation_status": row.validation_status,
        "checksum": row.checksum,
        "source_manifest": row.source_manifest,
        "metadata_json": row.metadata_json,
    }


def _dataset_row_to_dict(row: DatasetVersions | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "dataset_version_id": row.dataset_version_id,
        "dataset_name": row.dataset_name,
        "created_at": row.created_at,
        "source": row.source,
        "price_basis": row.price_basis,
        "interval": row.interval,
        "schema_version": row.schema_version,
        "symbol_coverage": row.symbol_coverage,
        "date_coverage_start": row.date_coverage_start,
        "date_coverage_end": row.date_coverage_end,
        "validation_status": row.validation_status,
        "checksum": row.checksum,
        "source_dataset_version": row.source_dataset_version,
        "source_manifest": row.source_manifest,
        "metadata_json": row.metadata_json,
    }


def _feature_row_to_contract(row: FeatureDatasetVersions) -> FeatureDatasetVersion:
    return FeatureDatasetVersion(
        dataset_version_id=row.dataset_version_id,
        feature_name=row.feature_name,
        dataset_name=row.dataset_name,
        created_at=row.created_at,
        schema_version=row.schema_version,
        source_dataset_version=row.source_dataset_version,
        underlying_price_basis=row.underlying_price_basis,
        computation_parameters=row.computation_parameters,
        computation_code_version=row.computation_code_version,
        storage_path=row.storage_path,
        symbol_coverage=row.symbol_coverage,
        date_coverage_start=row.date_coverage_start,
        date_coverage_end=row.date_coverage_end,
        validation_status=row.validation_status,
        checksum=row.checksum,
        source_manifest=row.source_manifest,
        metadata_json=row.metadata_json,
    )


def _feature_job_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if args.include_returns:
        specs.append(
            {
                "step": "returns_feature",
                "feature_name": "returns",
                "computation_parameters": {
                    "price_column": "close",
                    "horizons": [1, 5, 20],
                },
            }
        )
    if args.include_volatility:
        specs.append(
            {
                "step": "volatility_feature",
                "feature_name": "volatility",
                "computation_parameters": {
                    "price_column": "close",
                    "returns_column": "ret_1d",
                    "window": 20,
                    "output_column": "volatility_value",
                },
            }
        )
    if args.include_moving_average:
        for step, window in (
            ("moving_average_feature_short", 20),
            ("moving_average_feature_long", 50),
        ):
            specs.append(
                {
                    "step": step,
                    "feature_name": "moving_average",
                    "computation_parameters": {
                        "price_column": "close",
                        "window": window,
                        "output_column": "moving_average_value",
                    },
                }
            )
    if args.include_liquidity:
        specs.append(
            {
                "step": "liquidity_feature",
                "feature_name": "liquidity",
                "computation_parameters": {
                    "avg_volume_window": 20,
                    "volume_column": "volume",
                    "bid_column": "bid",
                    "ask_column": "ask",
                    "output_columns": ["avg_volume_value", "bid_ask_spread"],
                },
            }
        )
    if args.include_regime:
        specs.append(
            {
                "step": "regime_feature",
                "feature_name": "regime",
                "computation_parameters": {
                    "price_column": "close",
                    "short_window": 50,
                    "long_window": 200,
                    "output_column": "regime",
                },
            }
        )
    if args.include_regime_classification:
        specs.append(
            {
                "step": "regime_classification_feature",
                "feature_name": "regime_classification",
                "computation_parameters": {
                    "price_column": "close",
                    "volume_column": "volume",
                    "returns_column": "ret_1d",
                    "trend_short_window": 50,
                    "trend_long_window": 200,
                    "vol_window": 20,
                    "liquidity_avg_window": 20,
                    "zscore_window": 20,
                    "high_percentile": 80.0,
                    "low_percentile": 20.0,
                },
            }
        )
    return specs


def _validate_source_dataset(
    *,
    source_dataset: DatasetVersions | None,
    dataset_version_id: str,
    price_basis: PriceBasis,
) -> list[str]:
    warnings: list[str] = []
    if source_dataset is None:
        raise ValueError(f"Dataset version not found: {dataset_version_id}")

    expected_name = "adjusted_bars" if price_basis == PriceBasis.ADJUSTED else "raw_bars"
    if source_dataset.dataset_name != expected_name:
        raise MixedLineageError(
            f"{price_basis.name} feature pipeline must use a {expected_name} source dataset."
        )
    if source_dataset.price_basis != price_basis:
        raise MixedLineageError(
            f"Source dataset price_basis mismatch: expected {price_basis.value}, "
            f"got {source_dataset.price_basis.value}"
        )
    if source_dataset.validation_status != "validated":
        warnings.append(
            f"Source dataset {dataset_version_id} is {source_dataset.validation_status}, not validated."
        )
    if price_basis == PriceBasis.ADJUSTED and source_dataset.source_dataset_version is None:
        raise MixedLineageError(
            "ADJUSTED feature pipeline source dataset must link to a raw source dataset."
        )
    return warnings


def _build_pipeline_plan(args: argparse.Namespace) -> dict[str, Any]:
    price_basis = _parse_price_basis(args.price_basis)
    symbols = _parse_symbols(args.symbols, required=True)
    _validate_date_window(args.start_date, args.end_date)

    session = get_session()
    try:
        dataset_repository = DatasetVersionsRepository(session)
        feature_repository = FeatureDatasetVersionsRepository(session)
        source_dataset = dataset_repository.get_by_dataset_version_id(args.dataset_version_id)
        warnings = _validate_source_dataset(
            source_dataset=source_dataset,
            dataset_version_id=args.dataset_version_id,
            price_basis=price_basis,
        )

        planned_features: list[dict[str, Any]] = []
        for spec in _feature_job_specs(args):
            existing = feature_repository.find_matching_dataset(
                feature_name=spec["feature_name"],
                source_dataset_version_id=args.dataset_version_id,
                symbols=symbols,
                start_date=args.start_date,
                end_date=args.end_date,
                computation_parameters=spec["computation_parameters"],
            )
            planned_features.append(
                {
                    **spec,
                    "action": "reuse" if existing is not None else "compute",
                    "existing_feature_dataset_version_id": (
                        None if existing is None else existing.dataset_version_id
                    ),
                    "existing_storage_path": None if existing is None else existing.storage_path,
                }
            )

        return {
            "dry_run": True,
            "would_write": False,
            "would_fetch_external_data": False,
            "dataset_version_id": args.dataset_version_id,
            "source_dataset": _dataset_row_to_dict(source_dataset),
            "price_basis": price_basis,
            "symbols": symbols,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "universe_version_id": args.universe_version_id,
            "feature_steps": planned_features,
            "warnings": warnings,
        }
    finally:
        session.close()


def handle_plan_pipeline(args: argparse.Namespace) -> int:
    payload = _build_pipeline_plan(args)
    _emit(payload, getattr(args, "output", None))
    return 0


def handle_run_pipeline(args: argparse.Namespace) -> int:
    if args.dry_run:
        payload = _build_pipeline_plan(args)
        _emit(payload, args.output)
        return 0

    price_basis = _parse_price_basis(args.price_basis)
    symbols = _parse_symbols(args.symbols, required=True)
    _validate_date_window(args.start_date, args.end_date)

    setup_telemetry("cli-feature-pipeline")
    result = run_feature_pipeline_cycle(
        price_basis=price_basis,
        dataset_version_id=args.dataset_version_id,
        symbols=symbols,
        universe_version_id=args.universe_version_id,
        start_date=args.start_date,
        end_date=args.end_date,
        include_returns=args.include_returns,
        include_volatility=args.include_volatility,
        include_moving_average=args.include_moving_average,
        include_liquidity=args.include_liquidity,
        include_regime=args.include_regime,
        include_regime_classification=args.include_regime_classification,
    )
    _emit({"dry_run": False, **result}, args.output)
    return 0


def handle_list_datasets(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        stmt = select(FeatureDatasetVersions).order_by(FeatureDatasetVersions.created_at.desc())
        if args.feature_name:
            stmt = stmt.where(FeatureDatasetVersions.feature_name == args.feature_name)
        if args.source_dataset_version:
            stmt = stmt.where(
                FeatureDatasetVersions.source_dataset_version == args.source_dataset_version
            )
        if args.price_basis:
            stmt = stmt.where(
                FeatureDatasetVersions.underlying_price_basis
                == _parse_price_basis(args.price_basis)
            )
        if args.validated_only:
            stmt = stmt.where(FeatureDatasetVersions.validation_status == "validated")
        rows = list(session.scalars(stmt.limit(args.limit)).all())
        _emit(
            {
                "count": len(rows),
                "feature_dataset_versions": [_feature_row_to_dict(row) for row in rows],
            },
            args.output,
        )
        return 0
    finally:
        session.close()


def handle_inspect_dataset(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        feature_repository = FeatureDatasetVersionsRepository(session)
        dataset_repository = DatasetVersionsRepository(session)
        row = feature_repository.get_by_dataset_version_id(args.feature_dataset_version_id)
        if row is None:
            raise ValueError(
                f"Feature dataset version not found: {args.feature_dataset_version_id}"
            )
        source_dataset = dataset_repository.get_by_dataset_version_id(row.source_dataset_version)
        _emit(
            {
                "feature_dataset_version": _feature_row_to_dict(row),
                "source_dataset": _dataset_row_to_dict(source_dataset),
            },
            args.output,
        )
        return 0
    finally:
        session.close()


def handle_latest(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = FeatureDatasetVersionsRepository(session).get_latest_validated(
            feature_name=args.feature_name,
            underlying_price_basis=_parse_price_basis(args.price_basis),
        )
        _emit(
            {
                "found": row is not None,
                "feature_dataset_version": None if row is None else _feature_row_to_dict(row),
            },
            args.output,
        )
        return 0
    finally:
        session.close()


def _resolve_feature_storage_path(row: FeatureDatasetVersions) -> Path | None:
    candidates: list[Path] = []
    storage_path = Path(row.storage_path)
    if storage_path.is_absolute():
        candidates.append(storage_path)
    else:
        data_root = parquet_paths.get_data_root()
        candidates.append(data_root / storage_path)
        dataset = FEATURE_DATASETS_BY_NAME.get(row.feature_name)
        if dataset is not None:
            candidates.append(
                parquet_paths.dataset_version_root(
                    data_root,
                    dataset,
                    row.dataset_version_id,
                )
            )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def handle_validate_dataset(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = FeatureDatasetVersionsRepository(session).get_by_dataset_version_id(
            args.feature_dataset_version_id
        )
        if row is None:
            raise ValueError(
                f"Feature dataset version not found: {args.feature_dataset_version_id}"
            )
        result = FeatureDatasetValidationService().validate_feature_dataset(
            _feature_row_to_contract(row)
        )
        parquet_path = _resolve_feature_storage_path(row) if args.check_parquet else None
        _emit(
            {
                "ok": result.ok and (not args.check_parquet or parquet_path is not None),
                "metadata_ok": result.ok,
                "violations": [
                    {
                        "code": violation.code,
                        "field": violation.field,
                        "message": violation.message,
                    }
                    for violation in result.violations
                ],
                "check_parquet": args.check_parquet,
                "parquet_exists": None if not args.check_parquet else parquet_path is not None,
                "parquet_path": None if parquet_path is None else str(parquet_path),
                "feature_dataset_version": _feature_row_to_dict(row),
            },
            args.output,
        )
        return 0
    finally:
        session.close()


def handle_register_dataset_version(args: argparse.Namespace) -> int:
    computation_parameters = _load_json_object(
        args.computation_parameters_json,
        option_name="--computation-parameters-json",
    )
    if not computation_parameters:
        raise ValueError("--computation-parameters-json must be a non-empty JSON object")

    source_manifest = _load_json_object(
        args.source_manifest_json,
        option_name="--source-manifest-json",
    )
    metadata_json = _load_json_object(args.metadata_json, option_name="--metadata-json")

    service = FeatureDatasetCommandService(session_factory=get_session)
    dataset_version_id = service.create_feature_dataset_version(
        dataset_version_id=args.dataset_version_id,
        feature_name=args.feature_name,
        dataset_name=args.dataset_name,
        underlying_price_basis=_parse_price_basis(args.price_basis),
        underlying_dataset_version=args.source_dataset_version,
        schema_version=args.schema_version,
        validation_status=args.validation_status,
        computation_parameters=computation_parameters,
        computation_code_version=args.computation_code_version,
        storage_path=args.storage_path,
        source_manifest=source_manifest,
        metadata_json=metadata_json,
        symbol_coverage=args.symbol_coverage,
        date_coverage_start=args.date_coverage_start,
        date_coverage_end=args.date_coverage_end,
        checksum=args.checksum,
        created_at=datetime.now(UTC),
    )
    _emit(
        {
            "registered": True,
            "feature_dataset_version_id": dataset_version_id,
            "feature_name": args.feature_name,
            "source_dataset_version": args.source_dataset_version,
            "validation_status": args.validation_status,
        },
        args.output,
    )
    return 0


def handle_sample_dataset(args: argparse.Namespace) -> int:
    import pyarrow.dataset as ds

    session = get_session()
    try:
        row = FeatureDatasetVersionsRepository(session).get_by_dataset_version_id(
            args.feature_dataset_version_id
        )
        if row is None:
            raise ValueError(
                f"Feature dataset version not found: {args.feature_dataset_version_id}"
            )
        path = _resolve_feature_storage_path(row)
        if path is None:
            raise ValueError(
                f"Parquet path not found for feature dataset {args.feature_dataset_version_id}"
            )

        dataset = ds.dataset(str(path), format="parquet", partitioning="hive")
        filter_expr = None
        symbol = None
        if args.symbol:
            symbol = args.symbol.strip().upper()
            if "symbol" in dataset.schema.names:
                filter_expr = ds.field("symbol") == symbol
        table = dataset.to_table(filter=filter_expr).slice(0, args.limit)
        _emit(
            {
                "feature_dataset_version_id": row.dataset_version_id,
                "feature_name": row.feature_name,
                "symbol": symbol,
                "limit": args.limit,
                "row_count": table.num_rows,
                "rows": table.to_pylist(),
            },
            args.output,
        )
        return 0
    finally:
        session.close()


def handle_export_lineage(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        feature_repository = FeatureDatasetVersionsRepository(session)
        dataset_repository = DatasetVersionsRepository(session)
        row = feature_repository.get_by_dataset_version_id(args.feature_dataset_version_id)
        if row is None:
            raise ValueError(
                f"Feature dataset version not found: {args.feature_dataset_version_id}"
            )
        payload = {
            "feature_dataset_version": _feature_row_to_dict(row),
            "source_dataset": _dataset_row_to_dict(
                dataset_repository.get_by_dataset_version_id(row.source_dataset_version)
            ),
            "exported_at": datetime.now(UTC),
        }
        _emit(payload, args.output)
        return 0
    finally:
        session.close()


def handle_resolve_for_simulation(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols, required=True)
    _validate_date_window(args.start_date, args.end_date)
    price_basis = _parse_price_basis(args.price_basis)
    if args.features:
        feature_names = [feature.strip() for feature in args.features.split(",") if feature.strip()]
    else:
        try:
            feature_names = _FEATURES_BY_STRATEGY[args.strategy_type]
        except KeyError as exc:
            raise ValueError(
                "--features is required when --strategy-type is not one of "
                f"{sorted(_FEATURES_BY_STRATEGY)}"
            ) from exc

    unknown = [name for name in feature_names if name not in FEATURE_DATASETS_BY_NAME]
    if unknown:
        raise ValueError(f"Unsupported feature names: {', '.join(unknown)}")

    session = get_session()
    try:
        repository = FeatureDatasetVersionsRepository(session)
        dependencies: list[dict[str, Any]] = []
        for feature_name in feature_names:
            row = repository.find_for_simulation(
                feature_name=feature_name,
                source_dataset_version=args.source_dataset_version,
                price_basis=price_basis,
                start_date=args.start_date,
                end_date=args.end_date,
                min_symbol_count=len(symbols or []),
            )
            dependencies.append(
                {
                    "feature_name": feature_name,
                    "status": "resolved" if row is not None else "missing",
                    "feature_dataset_version": None if row is None else _feature_row_to_dict(row),
                }
            )

        _emit(
            {
                "strategy_type": args.strategy_type,
                "source_dataset_version": args.source_dataset_version,
                "price_basis": price_basis,
                "symbols": symbols,
                "start_date": args.start_date,
                "end_date": args.end_date,
                "all_resolved": all(dep["status"] == "resolved" for dep in dependencies),
                "dependencies": dependencies,
            },
            args.output,
        )
        return 0
    finally:
        session.close()


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    parser.add_argument("--output", default=None, help="Optional path to write JSON output.")


def _add_price_basis_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--price-basis",
        default="RAW",
        choices=["RAW", "ADJUSTED"],
    )


def _add_include_arguments(parser: argparse.ArgumentParser) -> None:
    for name in (
        "returns",
        "volatility",
        "moving-average",
        "liquidity",
        "regime",
        "regime-classification",
    ):
        parser.add_argument(
            f"--include-{name}",
            action=argparse.BooleanOptionalAction,
            default=True,
        )


def _add_pipeline_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset-version-id",
        required=True,
        help="Existing source dataset_version_id to compute features from.",
    )
    _add_price_basis_argument(parser)
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated symbols, e.g. AAPL,MSFT,SPY.",
    )
    parser.add_argument(
        "--universe-version-id",
        default=None,
        help="Optional universe version id to record in run metadata.",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    _add_include_arguments(parser)
    _add_output_argument(parser)


def register(subparsers) -> None:
    parser = subparsers.add_parser("features")
    feature_subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = feature_subparsers.add_parser("list-datasets")
    list_parser.add_argument("--feature-name", default=None)
    list_parser.add_argument("--source-dataset-version", default=None)
    list_parser.add_argument("--validated-only", action="store_true", default=False)
    list_parser.add_argument("--limit", type=int, default=20)
    _add_price_basis_argument(list_parser)
    list_parser.set_defaults(price_basis=None)
    _add_output_argument(list_parser)
    list_parser.set_defaults(func=handle_list_datasets)

    inspect_parser = feature_subparsers.add_parser("inspect-dataset")
    inspect_parser.add_argument("--feature-dataset-version-id", required=True)
    _add_output_argument(inspect_parser)
    inspect_parser.set_defaults(func=handle_inspect_dataset)

    latest_parser = feature_subparsers.add_parser("latest")
    latest_parser.add_argument("--feature-name", required=True)
    _add_price_basis_argument(latest_parser)
    _add_output_argument(latest_parser)
    latest_parser.set_defaults(func=handle_latest)

    validate_parser = feature_subparsers.add_parser("validate-dataset")
    validate_parser.add_argument("--feature-dataset-version-id", required=True)
    validate_parser.add_argument("--check-parquet", action="store_true", default=False)
    _add_output_argument(validate_parser)
    validate_parser.set_defaults(func=handle_validate_dataset)

    plan_parser = feature_subparsers.add_parser("plan-pipeline")
    _add_pipeline_scope_arguments(plan_parser)
    plan_parser.set_defaults(func=handle_plan_pipeline)

    run_parser = feature_subparsers.add_parser("run-pipeline")
    _add_pipeline_scope_arguments(run_parser)
    run_parser.add_argument("--dry-run", action="store_true", default=False)
    run_parser.set_defaults(func=handle_run_pipeline)

    register_parser = feature_subparsers.add_parser("register-dataset-version")
    register_parser.add_argument("--dataset-version-id", default=None)
    register_parser.add_argument("--feature-name", required=True)
    register_parser.add_argument("--dataset-name", default="features")
    register_parser.add_argument("--source-dataset-version", required=True)
    _add_price_basis_argument(register_parser)
    register_parser.add_argument("--schema-version", default="1.0")
    register_parser.add_argument("--validation-status", default="unvalidated")
    register_parser.add_argument("--computation-parameters-json", required=True)
    register_parser.add_argument("--computation-code-version", default="cli")
    register_parser.add_argument("--storage-path", required=True)
    register_parser.add_argument("--symbol-coverage", type=int, default=None)
    register_parser.add_argument("--date-coverage-start", type=date.fromisoformat, default=None)
    register_parser.add_argument("--date-coverage-end", type=date.fromisoformat, default=None)
    register_parser.add_argument("--checksum", default=None)
    register_parser.add_argument("--source-manifest-json", default=None)
    register_parser.add_argument("--metadata-json", default=None)
    _add_output_argument(register_parser)
    register_parser.set_defaults(func=handle_register_dataset_version)

    sample_parser = feature_subparsers.add_parser("sample-dataset")
    sample_parser.add_argument("--feature-dataset-version-id", required=True)
    sample_parser.add_argument("--symbol", default=None)
    sample_parser.add_argument("--limit", type=int, default=20)
    _add_output_argument(sample_parser)
    sample_parser.set_defaults(func=handle_sample_dataset)

    lineage_parser = feature_subparsers.add_parser("export-lineage")
    lineage_parser.add_argument("--feature-dataset-version-id", required=True)
    lineage_parser.add_argument("--output", required=True)
    lineage_parser.add_argument("--json", action="store_true")
    lineage_parser.set_defaults(func=handle_export_lineage)

    resolve_parser = feature_subparsers.add_parser("resolve-for-simulation")
    resolve_parser.add_argument("--strategy-type", required=True)
    resolve_parser.add_argument("--features", default=None)
    resolve_parser.add_argument("--source-dataset-version", required=True)
    _add_price_basis_argument(resolve_parser)
    resolve_parser.add_argument("--symbols", required=True)
    resolve_parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    resolve_parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    _add_output_argument(resolve_parser)
    resolve_parser.set_defaults(func=handle_resolve_for_simulation)
