from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.storage.sor.repositories.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.universe.jobs.run_universe_selection_cycle import (
    run_universe_selection_cycle,
)
from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)
from autonomous_trading_platform.universe.services.universe_membership_service import (
    UniverseMembershipService,
)
from autonomous_trading_platform.universe.services.universe_snapshot_service import (
    UniverseSnapshotService,
)
from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
)
from src.db import get_session


@dataclass
class UniverseDependencies:
    session: Session
    snapshot_repository: UniverseSnapshotRepository
    snapshot_service: UniverseSnapshotService
    validation_service: UniverseValidationService
    membership_service: UniverseMembershipService


def build_dependencies() -> UniverseDependencies:
    session = get_session()

    snapshot_repository = UniverseSnapshotRepository(session)
    snapshot_service = UniverseSnapshotService(snapshot_repository)
    validation_service = UniverseValidationService(session)

    ticker_lifecycle_repository = TickerLifecycleRepository(session)
    ticker_lifecycle_service = TickerLifecycleService(ticker_lifecycle_repository)

    membership_service = UniverseMembershipService(
        repository=snapshot_repository,
        ticker_lifecycle_service=ticker_lifecycle_service,
    )

    return UniverseDependencies(
        session=session,
        snapshot_repository=snapshot_repository,
        snapshot_service=snapshot_service,
        validation_service=validation_service,
        membership_service=membership_service,
    )


def register(subparsers) -> None:
    universe_parser = subparsers.add_parser("universe", help="Universe operations")
    universe_subparsers = universe_parser.add_subparsers(
        dest="universe_command",
        required=True,
    )

    select_now_parser = universe_subparsers.add_parser(
        "select-now",
        help="Run the universe selection cycle now",
    )
    select_now_parser.set_defaults(func=handle_select_now)

    inspect_active_parser = universe_subparsers.add_parser(
        "inspect-active",
        help="Inspect the active universe snapshot for a date",
    )
    inspect_active_parser.add_argument("--timestamp")
    inspect_active_parser.set_defaults(func=handle_inspect_active)

    inspect_symbols_parser = universe_subparsers.add_parser(
        "inspect-symbols",
        help="Inspect query symbols for a date",
    )
    inspect_symbols_parser.add_argument("--timestamp")
    inspect_symbols_parser.set_defaults(func=handle_inspect_symbols)

    inspect_symbol_parser = universe_subparsers.add_parser(
        "inspect-symbol",
        help="Check whether a symbol is in the universe for a date",
    )
    inspect_symbol_parser.add_argument("--symbol", required=True)
    inspect_symbol_parser.add_argument("--timestamp")
    inspect_symbol_parser.set_defaults(func=handle_inspect_symbol)

    validate_active_parser = universe_subparsers.add_parser(
        "validate-active",
        help="Validate the active universe snapshot for a date",
    )
    validate_active_parser.add_argument("--timestamp")
    validate_active_parser.set_defaults(func=handle_validate_active)

    inspect_ingestion_input_parser = universe_subparsers.add_parser(
        "inspect-ingestion-input",
        help="Show the symbol set ingestion should use for a date",
    )
    inspect_ingestion_input_parser.add_argument("--timestamp")
    inspect_ingestion_input_parser.set_defaults(func=handle_inspect_ingestion_input)


def _resolve_timestamp(raw: str | None) -> datetime:
    if raw:
        return parse_datetime(raw)
    return datetime.now(UTC)


def handle_select_now(_args: argparse.Namespace) -> int:
    run_universe_selection_cycle()
    print_header("Universe Select Now")
    print_json({"status": "success"})
    return 0


def handle_inspect_active(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    timestamp = _resolve_timestamp(args.timestamp)

    snapshot = deps.snapshot_repository.get_effective_for_date(timestamp)

    print_header("Inspect Active Universe")
    if snapshot is None:
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "found": False,
            }
        )
        return 0

    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "found": True,
            "universe_id": snapshot.universe_id,
            "snapshot_date": str(snapshot.snapshot_date),
            "effective_start": snapshot.effective_start,
            "effective_end": snapshot.effective_end,
            "symbol_count": len(snapshot.symbols),
            "symbols_preview": snapshot.symbols[:25],
            "criteria": snapshot.criteria,
            "version": snapshot.version,
            "source": snapshot.source,
            "built_at": snapshot.built_at,
        }
    )
    return 0


def handle_inspect_symbols(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    timestamp = _resolve_timestamp(args.timestamp)
    symbols = deps.membership_service.get_query_symbols_for_date(timestamp.date())

    print_header("Inspect Universe Symbols")
    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "symbol_count": len(symbols),
            "symbols": symbols,
        }
    )
    return 0


def handle_inspect_symbol(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    timestamp = _resolve_timestamp(args.timestamp)
    in_universe = deps.membership_service.is_resolved_symbol_in_universe_on_date(
        args.symbol,
        timestamp.date(),
    )

    print_header("Inspect Universe Symbol")
    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "symbol": args.symbol,
            "in_universe": in_universe,
        }
    )
    return 0


def handle_validate_active(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    timestamp = _resolve_timestamp(args.timestamp)

    snapshot = deps.snapshot_repository.get_effective_for_date(timestamp)
    if snapshot is None:
        raise RuntimeError(f"No active universe snapshot found for {timestamp.isoformat()}")

    result = deps.validation_service.validate_row(snapshot)

    print_header("Validate Active Universe")
    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "ok": result.ok,
            "errors": result.errors,
            "symbol_count": len(snapshot.symbols),
        }
    )
    return 0


def handle_inspect_ingestion_input(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    timestamp = _resolve_timestamp(args.timestamp)
    symbols = deps.membership_service.get_query_symbols_for_date(timestamp.date())

    print_header("Inspect Ingestion Input")
    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "expected_symbols_count": len(symbols),
            "expected_symbols": symbols,
        }
    )
    return 0
