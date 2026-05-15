from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.contracts.common.enums import UniverseSource
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
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
from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
)
from autonomous_trading_platform.universe.services.universe_version_service import (
    UniverseVersionService,
)


@dataclass
class UniverseDependencies:
    session: Session
    version_repository: UniverseVersionRepository
    version_service: UniverseVersionService
    validation_service: UniverseValidationService
    membership_service: UniverseMembershipService


def build_dependencies() -> UniverseDependencies:
    session = get_session()

    version_repository = UniverseVersionRepository(session)
    version_service = UniverseVersionService(version_repository)
    validation_service = UniverseValidationService(session)

    ticker_lifecycle_repository = TickerLifecycleRepository(session)
    ticker_lifecycle_service = TickerLifecycleService(ticker_lifecycle_repository)

    membership_service = UniverseMembershipService(
        repository=version_repository,
        ticker_lifecycle_service=ticker_lifecycle_service,
    )

    return UniverseDependencies(
        session=session,
        version_repository=version_repository,
        version_service=version_service,
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
    select_now_parser.add_argument("--timestamp")
    select_now_parser.set_defaults(func=handle_select_now)

    inspect_active_parser = universe_subparsers.add_parser(
        "inspect-active",
        help="Inspect the active universe version for a timestamp",
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
        help="Validate the active universe version for a timestamp",
    )
    validate_active_parser.add_argument("--timestamp")
    validate_active_parser.set_defaults(func=handle_validate_active)

    inspect_ingestion_input_parser = universe_subparsers.add_parser(
        "inspect-ingestion-input",
        help="Show the symbol set ingestion should use for a date",
    )
    inspect_ingestion_input_parser.add_argument("--timestamp")
    inspect_ingestion_input_parser.set_defaults(func=handle_inspect_ingestion_input)

    seed_parser = universe_subparsers.add_parser(
        "seed",
        help="Create a universe version from explicit symbols",
    )
    seed_parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols, e.g. SPY,AAPL,MSFT",
    )
    seed_parser.add_argument("--timestamp")
    seed_parser.add_argument(
        "--source",
        default=UniverseSource.CUSTOM,
        help="Source label for the seeded universe version",
    )
    seed_parser.add_argument(
        "--name",
        default=None,
        help="Human-readable name for the seeded universe version",
    )
    seed_parser.set_defaults(func=handle_seed)


def handle_seed(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        timestamp = _resolve_timestamp(args.timestamp)
        symbols = _parse_symbols(args.symbols)
        name = args.name or f"seed_{timestamp.date().isoformat()}"

        version, members = deps.version_service.build_version(
            name=name,
            effective_from=timestamp,
            symbols=symbols,
            source=args.source,
            rebalance_reason="manual_seed",
        )

        validation = deps.validation_service.validate_version_row(version, members)
        if not validation.ok:
            raise RuntimeError(validation.errors)

        deps.version_repository.retire_active_version(version.effective_from)
        deps.version_service.save_version(version, members)
        deps.version_repository.activate_version(version.universe_version_id)
        deps.session.commit()

        print_header("Seed Universe")
        print_json(
            {
                "status": "success",
                "timestamp": timestamp.isoformat(),
                "symbol_count": len(members),
                "symbols": [m.symbol for m in members],
                "source": args.source,
                "universe_version_id": version.universe_version_id,
                "name": version.name,
                "config_hash": version.config_hash,
            }
        )
        return 0
    finally:
        deps.session.close()


def _resolve_timestamp(raw: str | None) -> datetime:
    if raw:
        return parse_datetime(raw)
    return datetime.now(UTC)


def handle_select_now(args: argparse.Namespace) -> int:
    timestamp = _resolve_timestamp(args.timestamp)
    run_universe_selection_cycle(cycle_timestamp=timestamp)
    print_header("Universe Select Now")
    print_json(
        {
            "status": "success",
            "timestamp": timestamp.isoformat(),
        }
    )
    return 0


def handle_inspect_active(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    timestamp = _resolve_timestamp(args.timestamp)

    version = deps.version_repository.get_active_version(timestamp)

    print_header("Inspect Active Universe")
    if version is None:
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "found": False,
            }
        )
        return 0

    members = deps.version_repository.get_members(version.universe_version_id)
    symbols = [m.symbol for m in members]

    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "found": True,
            "universe_version_id": version.universe_version_id,
            "name": version.name,
            "effective_from": version.effective_from,
            "effective_to": version.effective_to,
            "status": version.status,
            "source": version.source,
            "rebalance_reason": version.rebalance_reason,
            "config_hash": version.config_hash,
            "created_at": version.created_at,
            "symbol_count": len(symbols),
            "symbols_preview": symbols[:25],
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

    version = deps.version_repository.get_active_version(timestamp)
    if version is None:
        raise RuntimeError(f"No active universe version found for {timestamp.isoformat()}")

    members = deps.version_repository.get_members(version.universe_version_id)
    result = deps.validation_service.validate_version_row(version, members)

    print_header("Validate Active Universe")
    print_json(
        {
            "timestamp": timestamp.isoformat(),
            "ok": result.ok,
            "errors": result.errors,
            "universe_version_id": version.universe_version_id,
            "symbol_count": len(members),
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


def _parse_symbols(raw: str) -> list[str]:
    symbols = [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")
    return symbols
