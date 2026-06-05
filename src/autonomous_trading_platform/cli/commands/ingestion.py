from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    floor_to_five_minutes,
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ingestion_checkpoints_repository import (
    IngestionCheckpointsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ingestion_runs_repository import (
    IngestionRunsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.market_bar_repository import (
    MarketBarRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.missing_bar_incidents_repository import (
    MissingBarIncidentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.symbol_date_coverage_repository import (
    SymbolDateCoverageRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)
from autonomous_trading_platform.universe.services.universe_membership_service import (
    UniverseMembershipService,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_DEFAULT_MAX_BACKFILL_DAYS = 31
_DEFAULT_MAX_SYMBOLS = 50


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _parse_symbols(raw: str) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []

    for part in raw.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if symbol in seen:
            duplicates.append(symbol)
            continue
        seen.add(symbol)
        symbols.append(symbol)

    if not symbols:
        raise ValueError("At least one symbol must be provided")
    if duplicates:
        raise ValueError(f"Duplicate symbols are not allowed: {', '.join(sorted(set(duplicates)))}")
    return symbols


def _parse_optional_symbols(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return _parse_symbols(raw)


def _validate_symbol_limit(symbols: list[str], max_symbols: int) -> None:
    if len(symbols) > max_symbols:
        raise ValueError(f"Symbol count {len(symbols)} exceeds --max-symbols {max_symbols}")


def _validate_backfill_window(start: datetime, end: datetime, max_days: int) -> None:
    if end < start:
        raise ValueError("--end must be greater than or equal to --start")

    span = end - start
    if span > timedelta(days=max_days):
        raise ValueError(f"Backfill window exceeds --max-days {max_days}")


def _price_basis(value: str) -> PriceBasis:
    try:
        return PriceBasis(value.lower())
    except ValueError as exc:
        choices = ", ".join(item.value for item in PriceBasis)
        raise argparse.ArgumentTypeError(f"--price-basis must be one of: {choices}") from exc


def register(subparsers) -> None:
    ingestion_parser = subparsers.add_parser("ingestion", help="Ingestion operations")
    ingestion_subparsers = ingestion_parser.add_subparsers(
        dest="ingestion_command",
        required=True,
    )

    plan_bars_parser = ingestion_subparsers.add_parser(
        "plan-bars",
        help="Plan bar ingestion without fetching or writing",
    )
    plan_bars_parser.add_argument("--timestamp")
    plan_bars_parser.add_argument(
        "--symbols",
        help="Comma-separated symbol override, e.g. SPY,AAPL",
    )
    plan_bars_parser.set_defaults(func=handle_plan_bars)

    run_bars_parser = ingestion_subparsers.add_parser(
        "run-bars",
        help="Run bar ingestion",
    )
    run_bars_parser.add_argument("--timestamp")
    run_bars_parser.add_argument(
        "--symbols",
        help="Comma-separated symbol override, e.g. SPY,AAPL",
    )
    run_bars_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the intended ingestion plan without fetching or writing",
    )
    run_bars_parser.add_argument(
        "--actor",
        help="Operator identity to include in run metadata for manual invocations",
    )
    run_bars_parser.set_defaults(func=handle_run_bars)

    run_backfill_parser = ingestion_subparsers.add_parser(
        "run-backfill",
        help="Run historical backfill",
    )
    run_backfill_parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols, e.g. SPY,AAPL,MSFT",
    )
    run_backfill_parser.add_argument("--start", required=True)
    run_backfill_parser.add_argument("--end", required=True)
    run_backfill_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize the backfill without fetching or writing",
    )
    run_backfill_parser.add_argument(
        "--max-days",
        type=_positive_int,
        default=_DEFAULT_MAX_BACKFILL_DAYS,
        help=f"Maximum allowed backfill window in days (default: {_DEFAULT_MAX_BACKFILL_DAYS})",
    )
    run_backfill_parser.add_argument(
        "--max-symbols",
        type=_positive_int,
        default=_DEFAULT_MAX_SYMBOLS,
        help=f"Maximum symbol count (default: {_DEFAULT_MAX_SYMBOLS})",
    )
    run_backfill_parser.add_argument(
        "--actor",
        help="Operator identity to include in run metadata for manual invocations",
    )
    run_backfill_parser.set_defaults(func=handle_run_backfill)

    run_corporate_actions_parser = ingestion_subparsers.add_parser(
        "run-corporate-actions",
        help="Run corporate actions ingestion",
    )
    run_corporate_actions_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate source raw-bars dataset without fetching or writing",
    )
    run_corporate_actions_parser.add_argument(
        "--source-raw-bars-dataset-version",
        dest="source_raw_bars_dataset_version",
        help="Use a specific raw-bars dataset version for deterministic adjusted-data generation",
    )
    run_corporate_actions_parser.add_argument(
        "--actor",
        help="Operator identity to include in run metadata for manual invocations",
    )
    run_corporate_actions_parser.set_defaults(func=handle_run_corporate_actions)

    inspect_bar_parser = ingestion_subparsers.add_parser(
        "inspect-bar",
        help="Inspect a stored market bar",
    )
    inspect_bar_parser.add_argument("--symbol", required=True)
    inspect_bar_parser.add_argument("--timestamp", required=True)
    inspect_bar_parser.set_defaults(func=handle_inspect_bar)

    inspect_run_parser = ingestion_subparsers.add_parser(
        "inspect-ingestion-run",
        help="Inspect a single ingestion run",
    )
    inspect_run_parser.add_argument("--ingestion-run-id", required=True)
    inspect_run_parser.set_defaults(func=handle_inspect_ingestion_run)

    list_runs_parser = ingestion_subparsers.add_parser(
        "list-ingestion-runs",
        help="List recent ingestion runs",
    )
    list_runs_parser.add_argument("--limit", type=_positive_int, default=20)
    list_runs_parser.add_argument("--status")
    list_runs_parser.set_defaults(func=handle_list_ingestion_runs)

    inspect_dataset_parser = ingestion_subparsers.add_parser(
        "inspect-dataset-version",
        help="Inspect dataset version metadata",
    )
    inspect_dataset_parser.add_argument("--dataset-version-id", required=True)
    inspect_dataset_parser.set_defaults(func=handle_inspect_dataset_version)

    latest_dataset_parser = ingestion_subparsers.add_parser(
        "latest-dataset",
        help="Show the latest validated dataset version",
    )
    latest_dataset_parser.add_argument("--dataset-name", required=True)
    latest_dataset_parser.add_argument("--price-basis", type=_price_basis, default=PriceBasis.RAW)
    latest_dataset_parser.set_defaults(func=handle_latest_dataset)

    inspect_checkpoint_parser = ingestion_subparsers.add_parser(
        "inspect-checkpoint",
        help="Inspect ingestion checkpoints",
    )
    inspect_checkpoint_parser.add_argument("--ingestion-run-id", required=True)
    inspect_checkpoint_parser.add_argument("--symbol")
    inspect_checkpoint_parser.set_defaults(func=handle_inspect_checkpoint)

    list_incidents_parser = ingestion_subparsers.add_parser(
        "list-incidents",
        help="List missing/late/outlier bar incidents",
    )
    list_incidents_parser.add_argument("--dataset-version", required=True)
    list_incidents_parser.add_argument("--symbol")
    list_incidents_parser.add_argument("--limit", type=_positive_int, default=100)
    list_incidents_parser.set_defaults(func=handle_list_incidents)

    inspect_coverage_parser = ingestion_subparsers.add_parser(
        "inspect-coverage",
        help="Inspect symbol/date coverage rows",
    )
    inspect_coverage_parser.add_argument("--dataset-version", required=True)
    inspect_coverage_parser.add_argument("--symbol", required=True)
    inspect_coverage_parser.set_defaults(func=handle_inspect_coverage)

    inspect_corporate_action_parser = ingestion_subparsers.add_parser(
        "inspect-corporate-action",
        help="Inspect stored corporate actions for a symbol",
    )
    inspect_corporate_action_parser.add_argument("--symbol", required=True)
    inspect_corporate_action_parser.add_argument("--limit", type=_positive_int, default=50)
    inspect_corporate_action_parser.set_defaults(func=handle_inspect_corporate_action)


def handle_plan_bars(args: argparse.Namespace) -> int:
    now_utc = parse_datetime(args.timestamp) if args.timestamp else datetime.now(UTC)
    symbols_override = _parse_optional_symbols(args.symbols)
    plan = _build_bars_plan(now_utc=now_utc, symbols_override=symbols_override)
    print_header("Plan Bars")
    print_json(plan)
    return 0


def handle_run_corporate_actions(args: argparse.Namespace) -> int:
    if args.dry_run:
        plan = _build_corporate_actions_plan(
            source_raw_bars_dataset_version_id=args.source_raw_bars_dataset_version,
            actor=args.actor,
        )
        print_header("Run Corporate Actions Dry Run")
        print_json(plan)
        return 0

    summary = run_corporate_action_ingestion_cycle(
        source_raw_bars_dataset_version_id=args.source_raw_bars_dataset_version,
        trigger_type="manual_cli",
        actor=args.actor,
    )
    print_header("Run Corporate Actions")
    print_json(
        {
            "pipeline": "corporate_actions_ingestion",
            "actor": args.actor,
            **summary,
            "status": "success",
        }
    )
    return 0


def handle_run_bars(args: argparse.Namespace) -> int:
    now_utc = parse_datetime(args.timestamp) if args.timestamp else None
    symbols_override = _parse_optional_symbols(args.symbols)

    if args.dry_run:
        plan = _build_bars_plan(
            now_utc=now_utc or datetime.now(UTC),
            symbols_override=symbols_override,
            actor=args.actor,
        )
        print_header("Run Bars Dry Run")
        print_json(plan)
        return 0

    summary = run_market_ingestion_cycle(
        now_utc=now_utc,
        symbols_override=symbols_override,
        trigger_type="manual_cli",
        actor=args.actor,
    )
    print_header("Run Bars")
    print_json({"actor": args.actor, **summary, "status": "success"})
    return 0


def handle_run_backfill(args: argparse.Namespace) -> int:
    start = parse_datetime(args.start)
    end = parse_datetime(args.end)
    symbols = _parse_symbols(args.symbols)
    _validate_symbol_limit(symbols, args.max_symbols)
    _validate_backfill_window(start, end, args.max_days)

    if args.dry_run:
        print_header("Run Backfill Dry Run")
        print_json(_build_backfill_plan(symbols=symbols, start=start, end=end, actor=args.actor))
        return 0

    summary = run_market_backfill_cycle(
        symbols=symbols,
        start=start,
        end=end,
        trigger_type="manual_cli",
        actor=args.actor,
    )

    print_header("Run Backfill")
    print_json(
        {
            "actor": args.actor,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "start": start.isoformat(),
            "end": end.isoformat(),
            **summary,
            "status": "success",
        }
    )
    return 0


def handle_inspect_bar(args: argparse.Namespace) -> int:
    print_header("Inspect Bar")
    timestamp = parse_datetime(args.timestamp)

    session = get_session()
    try:
        bar = MarketBarRepository(session).get_by_symbol_timestamp(
            symbol=args.symbol.upper(),
            timestamp=timestamp,
        )

        if bar is None:
            print_json(
                {
                    "symbol": args.symbol.upper(),
                    "timestamp": timestamp.isoformat(),
                    "found": False,
                }
            )
            return 1

        print_json(
            {
                "found": True,
                "bar": {
                    "bar_id": str(bar.bar_id),
                    "symbol": bar.symbol,
                    "timestamp": bar.timestamp.isoformat(),
                    "end_timestamp": (
                        bar.end_timestamp.isoformat()
                        if getattr(bar, "end_timestamp", None) is not None
                        else None
                    ),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                    "interval": (
                        bar.interval.value if hasattr(bar.interval, "value") else str(bar.interval)
                    ),
                    "price_basis": (
                        bar.price_basis.value
                        if hasattr(bar.price_basis, "value")
                        else str(bar.price_basis)
                    ),
                },
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_ingestion_run(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = IngestionRunsRepository(session).get_by_ingestion_run_id(args.ingestion_run_id)
        print_header("Inspect Ingestion Run")
        if row is None:
            print_json({"ingestion_run_id": args.ingestion_run_id, "found": False})
            return 1
        print_json({"found": True, "ingestion_run": _ingestion_run_to_dict(row)})
        return 0
    finally:
        session.close()


def handle_list_ingestion_runs(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = IngestionRunsRepository(session).list_recent(limit=args.limit, status=args.status)
        print_header("List Ingestion Runs")
        print_json(
            {
                "limit": args.limit,
                "status": args.status,
                "count": len(rows),
                "ingestion_runs": [_ingestion_run_to_dict(row) for row in rows],
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_dataset_version(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = DatasetVersionsRepository(session).get_by_dataset_version_id(args.dataset_version_id)
        print_header("Inspect Dataset Version")
        if row is None:
            print_json({"dataset_version_id": args.dataset_version_id, "found": False})
            return 1
        print_json({"found": True, "dataset_version": _dataset_version_to_dict(row)})
        return 0
    finally:
        session.close()


def handle_latest_dataset(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = DatasetVersionsRepository(session).get_latest_validated(
            dataset_name=args.dataset_name,
            price_basis=args.price_basis,
        )
        print_header("Latest Dataset")
        if row is None:
            print_json(
                {
                    "dataset_name": args.dataset_name,
                    "price_basis": args.price_basis.value,
                    "found": False,
                }
            )
            return 1
        print_json({"found": True, "dataset_version": _dataset_version_to_dict(row)})
        return 0
    finally:
        session.close()


def handle_inspect_checkpoint(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = IngestionCheckpointsRepository(session).list_by_ingestion_run_id(
            args.ingestion_run_id
        )
        if args.symbol:
            symbol = args.symbol.upper()
            rows = [row for row in rows if row.symbol == symbol]
        print_header("Inspect Checkpoints")
        print_json(
            {
                "ingestion_run_id": args.ingestion_run_id,
                "symbol": args.symbol.upper() if args.symbol else None,
                "count": len(rows),
                "checkpoints": [_checkpoint_to_dict(row) for row in rows],
            }
        )
        return 0 if rows else 1
    finally:
        session.close()


def handle_list_incidents(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = MissingBarIncidentsRepository(session).list_by_dataset_version(
            dataset_version=args.dataset_version,
            symbol=args.symbol.upper() if args.symbol else None,
        )[: args.limit]
        print_header("List Incidents")
        print_json(
            {
                "dataset_version": args.dataset_version,
                "symbol": args.symbol.upper() if args.symbol else None,
                "limit": args.limit,
                "count": len(rows),
                "incidents": [_incident_to_dict(row) for row in rows],
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_coverage(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        symbol = args.symbol.upper()
        rows = SymbolDateCoverageRepository(session).list_by_dataset_version_and_symbol(
            dataset_version=args.dataset_version,
            symbol=symbol,
        )
        print_header("Inspect Coverage")
        print_json(
            {
                "dataset_version": args.dataset_version,
                "symbol": symbol,
                "count": len(rows),
                "coverage": [_coverage_to_dict(row) for row in rows],
            }
        )
        return 0 if rows else 1
    finally:
        session.close()


def handle_inspect_corporate_action(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        symbol = args.symbol.upper()
        rows = CorporateActionRepository(session).list_by_symbol(symbol=symbol, limit=args.limit)
        print_header("Inspect Corporate Actions")
        print_json(
            {
                "symbol": symbol,
                "limit": args.limit,
                "count": len(rows),
                "corporate_actions": [_corporate_action_to_dict(row) for row in rows],
            }
        )
        return 0 if rows else 1
    finally:
        session.close()


def _build_bars_plan(
    *,
    now_utc: datetime,
    symbols_override: list[str] | None,
    actor: str | None = None,
) -> dict[str, Any]:
    cycle_end = floor_to_five_minutes(now_utc)
    cycle_start = cycle_end - timedelta(minutes=5)
    trading_date = cycle_end.date()

    session = get_session()
    try:
        active_universe = None
        if symbols_override is None:
            version_repository = UniverseVersionRepository(session)
            ticker_lifecycle_service = TickerLifecycleService(TickerLifecycleRepository(session))
            membership_service = UniverseMembershipService(
                repository=version_repository,
                ticker_lifecycle_service=ticker_lifecycle_service,
            )
            symbols = membership_service.get_query_symbols_for_date(trading_date)
            active_universe = version_repository.get_active_version(now_utc)
        else:
            symbols = symbols_override

        existing_dataset = _find_existing_active_daily_dataset(
            session=session,
            trading_date=trading_date,
        )

        return {
            "dry_run": True,
            "would_fetch_external_data": False,
            "would_write": False,
            "actor": actor,
            "timestamp": now_utc.isoformat(),
            "cycle_start": cycle_start.isoformat(),
            "cycle_end": cycle_end.isoformat(),
            "trading_date": trading_date.isoformat(),
            "dataset_name": RAW_BARS_DATASET.dataset_key,
            "price_basis": PriceBasis.RAW.value,
            "interval": BarInterval.FIVE_MIN.value,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "symbols_source": "override" if symbols_override is not None else "active_universe",
            "active_universe": _active_universe_to_dict(active_universe),
            "existing_active_daily_dataset_version": (
                _dataset_version_to_dict(existing_dataset) if existing_dataset is not None else None
            ),
            "would_create_dataset_version": existing_dataset is None,
        }
    finally:
        session.close()


def _build_backfill_plan(
    *,
    symbols: list[str],
    start: datetime,
    end: datetime,
    actor: str | None,
) -> dict[str, Any]:
    calendar_days = (end.date() - start.date()).days + 1
    trading_session_dates = [
        (start.date() + timedelta(days=offset)).isoformat()
        for offset in range(calendar_days)
        if (start.date() + timedelta(days=offset)).weekday() < 5
    ]
    return {
        "dry_run": True,
        "would_fetch_external_data": False,
        "would_write": False,
        "actor": actor,
        "pipeline": "market_backfill",
        "symbols": symbols,
        "symbol_count": len(symbols),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": calendar_days,
        "estimated_trading_sessions": len(trading_session_dates),
        "trading_session_dates": trading_session_dates,
        "estimated_symbol_days": len(symbols) * calendar_days,
        "estimated_symbol_sessions": len(symbols) * len(trading_session_dates),
        "dataset_name": RAW_BARS_DATASET.dataset_key,
        "price_basis": PriceBasis.RAW.value,
        "interval": BarInterval.FIVE_MIN.value,
    }


def _build_corporate_actions_plan(
    *,
    source_raw_bars_dataset_version_id: str | None,
    actor: str | None,
) -> dict[str, Any]:
    session = get_session()
    try:
        dataset_repository = DatasetVersionsRepository(session)
        if source_raw_bars_dataset_version_id is not None:
            source_dataset = dataset_repository.get_by_dataset_version_id(
                source_raw_bars_dataset_version_id
            )
            if source_dataset is None:
                raise ValueError(
                    "Source raw-bars dataset version not found: "
                    f"{source_raw_bars_dataset_version_id}"
                )
        else:
            source_dataset = dataset_repository.get_latest_validated(
                dataset_name=RAW_BARS_DATASET.dataset_key,
                price_basis=PriceBasis.RAW,
            )
            if source_dataset is None:
                raise ValueError("No validated raw bars dataset version found.")

        if source_dataset.dataset_name != RAW_BARS_DATASET.dataset_key:
            raise ValueError(f"Source dataset must be raw_bars; got {source_dataset.dataset_name}")
        if source_dataset.price_basis != PriceBasis.RAW:
            raise ValueError(
                "Source raw-bars dataset must use raw price basis; "
                f"got {_enum_value(source_dataset.price_basis)}"
            )

        return {
            "dry_run": True,
            "would_fetch_external_data": False,
            "would_write": False,
            "actor": actor,
            "pipeline": "corporate_actions_ingestion",
            "source_raw_bars_dataset_version": _dataset_version_to_dict(source_dataset),
        }
    finally:
        session.close()


def _find_existing_active_daily_dataset(
    *,
    session,
    trading_date,
) -> DatasetVersions | None:
    rows = (
        session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
        .filter(DatasetVersions.date_coverage_start == trading_date)
        .filter(DatasetVersions.date_coverage_end == trading_date)
        .order_by(DatasetVersions.created_at.desc())
        .all()
    )
    for row in rows:
        metadata = row.metadata_json or {}
        if (
            row.price_basis == PriceBasis.RAW
            and row.interval == BarInterval.FIVE_MIN
            and metadata.get("dataset_lifecycle") == "active_daily_incremental"
        ):
            return cast(DatasetVersions, row)
    return None


def _active_universe_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "universe_version_id": getattr(row, "universe_version_id", None),
        "source": _enum_value(getattr(row, "source", None)),
        "status": _enum_value(getattr(row, "status", None)),
    }


def _ingestion_run_to_dict(row) -> dict[str, Any]:
    return {
        "ingestion_run_id": row.ingestion_run_id,
        "created_at": _iso(row.created_at),
        "run_timestamp": _iso(row.run_timestamp),
        "run_type": _enum_value(row.run_type),
        "source": row.source,
        "dataset_version": row.dataset_version,
        "status": row.status,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "error_message": row.error_message,
        "row_count": row.row_count,
        "file_count": row.file_count,
    }


def _dataset_version_to_dict(row) -> dict[str, Any]:
    return {
        "dataset_version_id": row.dataset_version_id,
        "dataset_name": row.dataset_name,
        "created_at": _iso(row.created_at),
        "source": row.source,
        "price_basis": _enum_value(row.price_basis),
        "interval": _enum_value(row.interval),
        "schema_version": row.schema_version,
        "symbol_coverage": row.symbol_coverage,
        "date_coverage_start": _iso(row.date_coverage_start),
        "date_coverage_end": _iso(row.date_coverage_end),
        "validation_status": row.validation_status,
        "checksum": row.checksum,
        "source_dataset_version": row.source_dataset_version,
        "source_manifest": row.source_manifest,
        "metadata_json": row.metadata_json,
    }


def _checkpoint_to_dict(row) -> dict[str, Any]:
    return {
        "checkpoint_id": row.checkpoint_id,
        "ingestion_run_id": row.ingestion_run_id,
        "dataset_version": row.dataset_version,
        "checkpoint_scope": _enum_value(row.checkpoint_scope),
        "checkpoint_status": _enum_value(row.checkpoint_status),
        "symbol": row.symbol,
        "checkpoint_date": _iso(row.checkpoint_date),
        "cycle_timestamp": _iso(row.cycle_timestamp),
        "last_successful_bar_timestamp": _iso(row.last_successful_bar_timestamp),
        "retry_count": row.retry_count,
        "error_message": row.error_message,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _incident_to_dict(row) -> dict[str, Any]:
    return {
        "incident_id": row.incident_id,
        "symbol": row.symbol,
        "bar_timestamp": _iso(row.bar_timestamp),
        "dataset_version": row.dataset_version,
        "run_id": row.run_id,
        "ingestion_run_id": row.ingestion_run_id,
        "incident_type": row.incident_type,
        "severity": row.severity,
        "message": row.message,
        "created_at": _iso(row.created_at),
        "resolved_flag": row.resolved_flag,
    }


def _coverage_to_dict(row) -> dict[str, Any]:
    return {
        "coverage_id": row.coverage_id,
        "symbol": row.symbol,
        "date": _iso(row.date),
        "dataset_version": row.dataset_version,
        "expected_bar_count": row.expected_bar_count,
        "actual_bar_count": row.actual_bar_count,
        "completeness_status": row.completeness_status,
        "gap_summary": row.gap_summary,
        "updated_at": _iso(row.updated_at),
    }


def _corporate_action_to_dict(row) -> dict[str, Any]:
    return {
        "action_id": row.action_id,
        "symbol": row.symbol,
        "action_type": _enum_value(row.action_type),
        "effective_date": _iso(row.effective_date),
        "announced_date": _iso(row.announced_date),
        "record_date": _iso(row.record_date),
        "payable_date": _iso(row.payable_date),
        "split_ratio": row.split_ratio,
        "cash_amount": str(row.cash_amount) if row.cash_amount is not None else None,
        "currency": row.currency,
        "new_symbol": row.new_symbol,
        "source": row.source,
        "ingested_at": _iso(row.ingested_at),
        "metadata": row.meta,
    }
