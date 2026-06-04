from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.contracts.common.enums import UniverseSource
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core import (
    universe_rebalance_repository,
    universe_rotation_repository,
)
from autonomous_trading_platform.storage.sor.repositories.core.raw_market_pool_repository import (
    RawMarketPoolRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.storage.sor.services.raw_market_pool_query_service import (
    RawMarketPoolQueryService,
)
from autonomous_trading_platform.universe.jobs.run_candidate_generation import (
    run_candidate_generation,
)
from autonomous_trading_platform.universe.jobs.run_raw_market_pool_refresh import (
    run_raw_market_pool_refresh,
)
from autonomous_trading_platform.universe.jobs.run_rebalance import run_rebalance
from autonomous_trading_platform.universe.jobs.run_universe_rollback import run_universe_rollback
from autonomous_trading_platform.universe.jobs.run_universe_rotation import run_universe_rotation
from autonomous_trading_platform.universe.jobs.run_universe_selection_cycle import (
    run_universe_selection_cycle,
)
from autonomous_trading_platform.universe.services.raw_market_pool_refresh_service import (
    RawMarketPoolRefreshPreview,
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


def _build_raw_pool_query_service(session: Session) -> RawMarketPoolQueryService:
    return RawMarketPoolQueryService(RawMarketPoolRepository(session))


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
    select_now_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview selection without persisting or activating it",
    )
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

    inspect_version_parser = universe_subparsers.add_parser(
        "inspect-version",
        help="Inspect any universe version by ID",
    )
    inspect_version_parser.add_argument("--version-id", required=True)
    inspect_version_parser.add_argument("--output", default=None)
    inspect_version_parser.set_defaults(func=handle_inspect_version)

    validate_active_parser = universe_subparsers.add_parser(
        "validate-active",
        help="Validate the active universe version for a timestamp",
    )
    validate_active_parser.add_argument("--timestamp")
    validate_active_parser.set_defaults(func=handle_validate_active)

    validate_version_parser = universe_subparsers.add_parser(
        "validate-version",
        help="Validate any universe version by ID",
    )
    validate_version_parser.add_argument("--version-id", required=True)
    validate_version_parser.set_defaults(func=handle_validate_version)

    validation_report_parser = universe_subparsers.add_parser(
        "validation-report",
        help="Run active universe validation and print an operational report",
    )
    validation_report_parser.add_argument("--timestamp")
    validation_report_parser.set_defaults(func=handle_validation_report)

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
    seed_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview the seed without persisting or activating it",
    )
    seed_parser.add_argument("--approved-by", default=None)
    seed_parser.set_defaults(func=handle_seed)

    raw_pool_refresh_parser = universe_subparsers.add_parser(
        "raw-pool-refresh",
        help="Refresh the raw market symbol pool from the broker",
    )
    raw_pool_refresh_parser.add_argument("--timestamp")
    raw_pool_refresh_parser.add_argument(
        "--cadence",
        default=None,
        help="Refresh cadence override (daily|weekly). Defaults to settings.",
    )
    raw_pool_refresh_parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh even on non-trading days",
    )
    raw_pool_refresh_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and diff the broker symbol pool without persisting a snapshot",
    )
    raw_pool_refresh_parser.set_defaults(func=handle_raw_pool_refresh)

    raw_pool_inspect_parser = universe_subparsers.add_parser(
        "raw-pool-inspect",
        help="Inspect the latest raw market symbol pool snapshot",
    )
    raw_pool_inspect_parser.add_argument("--timestamp")
    raw_pool_inspect_parser.add_argument("--snapshot-id", default=None)
    raw_pool_inspect_parser.add_argument("--asset-type", default=None)
    raw_pool_inspect_parser.add_argument("--exchange", default=None)
    raw_pool_inspect_parser.set_defaults(func=handle_raw_pool_inspect)

    raw_pool_history_parser = universe_subparsers.add_parser(
        "raw-pool-history",
        help="List recent raw market pool snapshots",
    )
    raw_pool_history_parser.add_argument("--limit", type=_bounded_limit, default=20)
    raw_pool_history_parser.set_defaults(func=handle_raw_pool_history)

    raw_pool_inspect_symbol_parser = universe_subparsers.add_parser(
        "raw-pool-inspect-symbol",
        help="Check whether a symbol is in the raw market pool",
    )
    raw_pool_inspect_symbol_parser.add_argument("--symbol", required=True)
    raw_pool_inspect_symbol_parser.add_argument("--timestamp")
    raw_pool_inspect_symbol_parser.set_defaults(func=handle_raw_pool_inspect_symbol)

    candidate_generate_parser = universe_subparsers.add_parser(
        "candidate-generate",
        help="Generate a candidate universe version from the raw market pool",
    )
    candidate_generate_parser.add_argument("--timestamp")
    candidate_generate_parser.add_argument("--lookback-days", type=_positive_int, default=20)
    candidate_generate_parser.add_argument(
        "--min-price",
        type=_positive_decimal,
        default=Decimal("1.00"),
    )
    candidate_generate_parser.add_argument(
        "--min-addv", type=_positive_decimal, default=Decimal("5000000")
    )
    candidate_generate_parser.add_argument("--max-symbols", type=_positive_int, default=500)
    candidate_generate_parser.add_argument("--name", default=None)
    candidate_generate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score and summarize the candidate without persisting it",
    )
    candidate_generate_parser.add_argument("--approved-by", default=None)
    candidate_generate_parser.set_defaults(func=handle_candidate_generate)

    candidate_inspect_parser = universe_subparsers.add_parser(
        "candidate-inspect",
        help="Inspect a candidate universe version",
    )
    candidate_inspect_parser.add_argument("--version-id", default=None)
    candidate_inspect_parser.add_argument("--timestamp")
    candidate_inspect_parser.set_defaults(func=handle_candidate_inspect)

    candidate_inspect_rejections_parser = universe_subparsers.add_parser(
        "candidate-inspect-rejections",
        help="Show rejected symbols for a candidate universe version",
    )
    candidate_inspect_rejections_parser.add_argument("--version-id", required=True)
    candidate_inspect_rejections_parser.add_argument("--reason", default=None)
    candidate_inspect_rejections_parser.set_defaults(func=handle_candidate_inspect_rejections)

    candidate_inspect_symbol_parser = universe_subparsers.add_parser(
        "candidate-inspect-symbol",
        help="Check a symbol's inclusion status in a candidate universe",
    )
    candidate_inspect_symbol_parser.add_argument("--symbol", required=True)
    candidate_inspect_symbol_parser.add_argument("--version-id", default=None)
    candidate_inspect_symbol_parser.set_defaults(func=handle_candidate_inspect_symbol)

    history_parser = universe_subparsers.add_parser(
        "history",
        help="List recent universe versions (all statuses)",
    )
    history_parser.add_argument(
        "--limit",
        type=_bounded_limit,
        default=20,
        help="Max versions to return",
    )
    history_parser.add_argument(
        "--status", default=None, help="Filter by status (active|candidate|proposed|retired)"
    )
    history_parser.set_defaults(func=handle_history)

    export_version_parser = universe_subparsers.add_parser(
        "export-version",
        help="Export a universe version and members as a JSON artifact",
    )
    export_version_parser.add_argument("--version-id", required=True)
    export_version_parser.add_argument("--output", required=True)
    export_version_parser.set_defaults(func=handle_export_version)

    import_version_parser = universe_subparsers.add_parser(
        "import-version",
        help="Validate and import a universe version JSON artifact",
    )
    import_version_parser.add_argument("--input", required=True)
    import_version_parser.add_argument("--timestamp")
    import_version_parser.add_argument("--name", default=None)
    import_version_parser.add_argument("--dry-run", action="store_true")
    import_version_parser.add_argument(
        "--activate",
        action="store_true",
        help="Retire current active version and activate the imported version",
    )
    import_version_parser.add_argument("--approved-by", default=None)
    import_version_parser.set_defaults(func=handle_import_version)

    runtime_status_parser = universe_subparsers.add_parser(
        "runtime-status",
        help="Show active universe resolution summary for runtime pipelines",
    )
    runtime_status_parser.add_argument("--timestamp")
    runtime_status_parser.set_defaults(func=handle_runtime_status)

    observability_status_parser = universe_subparsers.add_parser(
        "observability-status",
        help="Show universe observability coverage and latest linkage status",
    )
    observability_status_parser.add_argument("--timestamp")
    observability_status_parser.set_defaults(func=handle_observability_status)

    propose_rebalance_parser = universe_subparsers.add_parser(
        "propose-rebalance",
        help="Propose a rebalanced universe from the latest candidate and active universe",
    )
    propose_rebalance_parser.add_argument(
        "--candidate-version-id",
        default=None,
        help="Candidate universe version ID (defaults to most recent candidate)",
    )
    propose_rebalance_parser.add_argument(
        "--active-version-id",
        default=None,
        help="Active universe version ID (defaults to current active at --timestamp)",
    )
    propose_rebalance_parser.add_argument("--timestamp")
    propose_rebalance_parser.add_argument(
        "--target-size", type=_positive_int, default=500, help="Target proposed universe size"
    )
    propose_rebalance_parser.add_argument(
        "--max-churn-pct",
        type=_pct,
        default=0.15,
        help="Maximum churn fraction allowed (0.0–1.0)",
    )
    propose_rebalance_parser.add_argument(
        "--retain-threshold",
        type=_positive_int,
        default=650,
        help="Retain active symbols with candidate rank <= this",
    )
    propose_rebalance_parser.add_argument(
        "--add-threshold",
        type=_positive_int,
        default=400,
        help="Only add new symbols with candidate rank <= this",
    )
    propose_rebalance_parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore max churn limit and apply all eligible changes",
    )
    propose_rebalance_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the proposal without persisting anything",
    )
    propose_rebalance_parser.add_argument("--name", default=None)
    propose_rebalance_parser.add_argument("--approved-by", default=None)
    propose_rebalance_parser.set_defaults(func=handle_propose_rebalance)

    rotate_parser = universe_subparsers.add_parser(
        "rotate",
        help="Run scheduled universe rotation (propose -> validate -> activate)",
    )
    rotate_parser.add_argument("--candidate-version-id", default=None)
    rotate_parser.add_argument("--timestamp")
    rotate_parser.add_argument("--target-size", type=_positive_int, default=500)
    rotate_parser.add_argument("--max-churn-pct", type=_pct, default=0.15)
    rotate_parser.add_argument("--retain-threshold", type=_positive_int, default=650)
    rotate_parser.add_argument("--add-threshold", type=_positive_int, default=400)
    rotate_parser.add_argument("--rotation-reason", default=None)
    rotate_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass churn limit and skip-if-unchanged check",
    )
    rotate_parser.add_argument(
        "--skip-cadence-check",
        action="store_true",
        help="Run regardless of configured cadence (useful for manual triggers)",
    )
    rotate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the rotation result without persisting",
    )
    rotate_parser.add_argument("--approved-by", default=None)
    rotate_parser.set_defaults(func=handle_rotate)

    rollback_parser = universe_subparsers.add_parser(
        "rollback",
        help="Rollback the active universe to a prior version",
    )
    rollback_parser.add_argument(
        "--target-version-id",
        required=True,
        help="universe_version_id to roll back to (must be a previously active/retired version)",
    )
    rollback_parser.add_argument(
        "--reason",
        required=True,
        help="Human-readable reason for the rollback (required for audit trail)",
    )
    rollback_parser.add_argument("--approved-by", default=None)
    rollback_parser.add_argument("--timestamp")
    rollback_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the rollback result without persisting",
    )
    rollback_parser.set_defaults(func=handle_rollback)

    rotation_history_parser = universe_subparsers.add_parser(
        "rotation-history",
        help="Show recent universe rotation audit records",
    )
    rotation_history_parser.add_argument("--limit", type=_bounded_limit, default=20)
    rotation_history_parser.set_defaults(func=handle_rotation_history)

    inspect_rotation_parser = universe_subparsers.add_parser(
        "inspect-rotation",
        help="Inspect one universe rotation or rollback record",
    )
    inspect_rotation_parser.add_argument("--rotation-id", required=True)
    inspect_rotation_parser.set_defaults(func=handle_inspect_rotation)

    rebalance_history_parser = universe_subparsers.add_parser(
        "rebalance-history",
        help="Show recent universe rebalance records",
    )
    rebalance_history_parser.add_argument("--limit", type=_bounded_limit, default=20)
    rebalance_history_parser.set_defaults(func=handle_rebalance_history)

    inspect_rebalance_parser = universe_subparsers.add_parser(
        "inspect-rebalance",
        help="Inspect one universe rebalance run",
    )
    inspect_rebalance_parser.add_argument("--rebalance-run-id", required=True)
    inspect_rebalance_parser.set_defaults(func=handle_inspect_rebalance)

    rotation_status_parser = universe_subparsers.add_parser(
        "rotation-status",
        help="Show the latest universe rotation record",
    )
    rotation_status_parser.set_defaults(func=handle_rotation_status)

    history_for_date_parser = universe_subparsers.add_parser(
        "history-for-date",
        help="Show the active universe version for a specific historical date",
    )
    history_for_date_parser.add_argument(
        "--timestamp",
        required=True,
        help="ISO-8601 timestamp to query the active universe for",
    )
    history_for_date_parser.set_defaults(func=handle_history_for_date)

    replay_timeline_parser = universe_subparsers.add_parser(
        "replay-timeline",
        help="Show all universe versions active during a date window (for replay inspection)",
    )
    replay_timeline_parser.add_argument(
        "--start",
        required=True,
        help="Window start (ISO-8601 timestamp or date)",
    )
    replay_timeline_parser.add_argument(
        "--end",
        required=True,
        help="Window end (ISO-8601 timestamp or date)",
    )
    replay_timeline_parser.set_defaults(func=handle_replay_timeline)

    compare_universes_parser = universe_subparsers.add_parser(
        "compare-universes",
        help="Compare the included symbol sets of two universe versions",
    )
    compare_universes_parser.add_argument("--version-a", required=True)
    compare_universes_parser.add_argument("--version-b", required=True)
    compare_universes_parser.add_argument("--output", default=None)
    compare_universes_parser.set_defaults(func=handle_compare_universes)

    symbol_history_parser = universe_subparsers.add_parser(
        "symbol-history",
        help="Show all universe versions that included a given symbol",
    )
    symbol_history_parser.add_argument("--symbol", required=True)
    symbol_history_parser.add_argument(
        "--start",
        default=None,
        help="Optional window start (ISO-8601)",
    )
    symbol_history_parser.add_argument(
        "--end",
        default=None,
        help="Optional window end (ISO-8601)",
    )
    symbol_history_parser.set_defaults(func=handle_symbol_history)

    lifecycle_symbol_parser = universe_subparsers.add_parser(
        "lifecycle-symbol",
        help="Show ticker lifecycle events and universe membership history for a symbol",
    )
    lifecycle_symbol_parser.add_argument("--symbol", required=True)
    lifecycle_symbol_parser.add_argument("--timestamp", default=None)
    lifecycle_symbol_parser.set_defaults(func=handle_lifecycle_symbol)


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

        if getattr(args, "dry_run", False):
            print_header("Seed Universe")
            print_json(
                {
                    "dry_run": True,
                    "status": "preview",
                    "timestamp": timestamp.isoformat(),
                    "symbol_count": len(members),
                    "symbols": [m.symbol for m in members],
                    "source": args.source,
                    "universe_version_id": version.universe_version_id,
                    "name": version.name,
                    "config_hash": version.config_hash,
                    "validation_ok": validation.ok,
                    "approved_by": getattr(args, "approved_by", None),
                }
            )
            deps.session.rollback()
            return 0

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
                "approved_by": getattr(args, "approved_by", None),
                "operator_identity_warning": None
                if getattr(args, "approved_by", None)
                else "--approved-by is recommended for manual seed mutations",
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
    result = run_universe_selection_cycle(
        cycle_timestamp=timestamp,
        dry_run=getattr(args, "dry_run", False),
    )
    print_header("Universe Select Now")
    if result is None:
        print_json(
            {
                "dry_run": getattr(args, "dry_run", False),
                "status": "skipped",
                "reason": "cadence_check_failed",
                "timestamp": timestamp.isoformat(),
            }
        )
        return 0
    print_json(
        {
            "dry_run": getattr(args, "dry_run", False),
            "status": "preview" if getattr(args, "dry_run", False) else "success",
            "timestamp": timestamp.isoformat(),
            "universe_version_id": result.version.universe_version_id,
            "name": result.version.name,
            "included_count": len(result.included_members),
            "excluded_count": len(result.excluded_members),
            "top_10_symbols": [m.symbol for m in result.included_members[:10]],
        }
    )
    return 0


def handle_inspect_active(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
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
    finally:
        deps.session.close()


def handle_inspect_symbols(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
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
    finally:
        deps.session.close()


def handle_inspect_symbol(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
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
    finally:
        deps.session.close()


def handle_inspect_version(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        version = deps.version_repository.get_by_version_id(args.version_id)
        print_header("Inspect Universe Version")
        if version is None:
            payload = {"found": False, "universe_version_id": args.version_id}
            _emit_payload(payload, args)
            return 1

        members = deps.version_repository.get_members(version.universe_version_id)
        payload = _version_payload(version, members, include_members=True)
        payload["found"] = True
        _emit_payload(payload, args)
        return 0
    finally:
        deps.session.close()


def handle_validate_active(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        timestamp = _resolve_timestamp(args.timestamp)
        version = deps.version_repository.get_active_version(timestamp)
        result = deps.validation_service.validate_active(timestamp)
        members = (
            deps.version_repository.get_members(version.universe_version_id)
            if version is not None
            else []
        )

        print_header("Validate Active Universe")
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "ok": result.ok,
                "errors": result.errors,
                "universe_version_id": version.universe_version_id if version else None,
                "symbol_count": len(members),
            }
        )
        return 0 if result.ok else 1
    finally:
        deps.session.close()


def handle_validation_report(args: argparse.Namespace) -> int:
    return handle_validate_active(args)


def handle_validate_version(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        version = deps.version_repository.get_by_version_id(args.version_id)
        print_header("Validate Universe Version")
        if version is None:
            print_json({"found": False, "universe_version_id": args.version_id})
            return 1

        members = deps.version_repository.get_members(version.universe_version_id)
        result = deps.validation_service.validate_version_row(version, members)
        print_json(
            {
                "found": True,
                "ok": result.ok,
                "errors": result.errors,
                "universe_version_id": version.universe_version_id,
                "status": version.status,
                "symbol_count": len(members),
                "included_count": len([m for m in members if m.excluded_reason is None]),
                "excluded_count": len([m for m in members if m.excluded_reason is not None]),
            }
        )
        return 0 if result.ok else 1
    finally:
        deps.session.close()


def handle_inspect_ingestion_input(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
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
    finally:
        deps.session.close()


def handle_raw_pool_refresh(args: argparse.Namespace) -> int:
    timestamp = _resolve_timestamp(args.timestamp)
    result = run_raw_market_pool_refresh(
        cadence=args.cadence,
        captured_at=timestamp,
        force=args.force,
        dry_run=getattr(args, "dry_run", False),
    )
    print_header("Raw Market Pool Refresh")
    if result is None:
        print_json(
            {
                "dry_run": getattr(args, "dry_run", False),
                "status": "skipped",
                "reason": "calendar_check_failed",
                "timestamp": timestamp.isoformat(),
            }
        )
        return 0
    if isinstance(result, RawMarketPoolRefreshPreview):
        print_json(
            {
                "dry_run": True,
                "status": "preview",
                "timestamp": timestamp.isoformat(),
                "source": result.source,
                "cadence": result.cadence,
                "symbol_count": result.symbol_count,
                "previous_snapshot_id": result.previous_snapshot_id,
                "previous_symbol_count": result.previous_symbol_count,
                "new_symbol_count": len(result.new_symbols),
                "delisted_symbol_count": len(result.delisted_symbols),
                "new_symbols_preview": result.new_symbols[:25],
                "delisted_symbols_preview": result.delisted_symbols[:25],
                "symbols_preview": result.symbols_preview,
            }
        )
        return 0
    if not isinstance(result, tuple):
        raise TypeError(f"Unexpected raw pool refresh result: {type(result)!r}")
    snapshot, new_symbols, delisted_symbols = cast(tuple[Any, list[str], list[str]], result)
    print_json(
        {
            "dry_run": False,
            "status": "success",
            "timestamp": timestamp.isoformat(),
            "snapshot_id": snapshot.snapshot_id,
            "symbol_count": snapshot.symbol_count,
            "new_symbol_count": len(new_symbols),
            "delisted_symbol_count": len(delisted_symbols),
        }
    )
    return 0


def handle_raw_pool_inspect(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        timestamp = _resolve_timestamp(args.timestamp)
        repository = RawMarketPoolRepository(session)
        query_service = RawMarketPoolQueryService(repository)

        snapshot_id = getattr(args, "snapshot_id", None)
        snapshot = (
            repository.get_snapshot_by_id(snapshot_id)
            if snapshot_id
            else query_service.get_snapshot_as_of(timestamp)
        )
        print_header("Raw Market Pool Inspect")
        if snapshot is None:
            print_json(
                {
                    "timestamp": timestamp.isoformat(),
                    "snapshot_id": snapshot_id,
                    "found": False,
                }
            )
            return 0

        memberships = repository.get_memberships_filtered(
            snapshot.snapshot_id,
            asset_type=getattr(args, "asset_type", None),
            exchange=getattr(args, "exchange", None),
            tradable_only=True,
        )
        symbols = [m.symbol for m in memberships]
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "found": True,
                "snapshot_id": snapshot.snapshot_id,
                "captured_at": snapshot.captured_at.isoformat(),
                "source": snapshot.source,
                "symbol_count": snapshot.symbol_count,
                "cadence": snapshot.cadence,
                "filtered_symbol_count": len(symbols),
                "symbols_preview": symbols[:25],
            }
        )
        return 0
    finally:
        session.close()


def handle_raw_pool_history(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        limit = getattr(args, "limit", 20)
        snapshots = RawMarketPoolRepository(session).get_recent_snapshots(limit=limit)

        print_header("Raw Market Pool History")
        print_json(
            {
                "total_returned": len(snapshots),
                "snapshots": [
                    {
                        "snapshot_id": s.snapshot_id,
                        "captured_at": s.captured_at.isoformat() if s.captured_at else None,
                        "source": s.source,
                        "symbol_count": s.symbol_count,
                        "cadence": s.cadence,
                        "is_complete": s.is_complete,
                        "metadata": s.metadata_json,
                    }
                    for s in snapshots
                ],
            }
        )
        return 0
    finally:
        session.close()


def handle_raw_pool_inspect_symbol(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        timestamp = _resolve_timestamp(args.timestamp)
        query_service = _build_raw_pool_query_service(session)

        eligible = query_service.is_symbol_eligible(args.symbol.strip().upper(), timestamp)
        raw_sym = query_service.get_raw_market_symbol(args.symbol.strip().upper())

        print_header("Raw Pool Inspect Symbol")
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "symbol": args.symbol.strip().upper(),
                "eligible": eligible,
                "first_seen": raw_sym.first_seen.isoformat() if raw_sym else None,
                "last_seen": raw_sym.last_seen.isoformat() if raw_sym else None,
                "status": raw_sym.status if raw_sym else None,
                "is_tradable": raw_sym.is_tradable if raw_sym else None,
            }
        )
        return 0
    finally:
        session.close()


def handle_candidate_generate(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.universe.types import CandidateGenerationConfig

    timestamp = _resolve_timestamp(args.timestamp)
    config = CandidateGenerationConfig(
        as_of=timestamp,
        lookback_days=args.lookback_days,
        min_price=args.min_price,
        min_average_daily_dollar_volume=args.min_addv,
        max_symbols=args.max_symbols,
    )
    result = run_candidate_generation(
        config=config,
        name=args.name,
        dry_run=getattr(args, "dry_run", False),
    )

    print_header("Candidate Generate")
    print_json(
        {
            "dry_run": getattr(args, "dry_run", False),
            "status": "preview" if getattr(args, "dry_run", False) else "success",
            "universe_version_id": result.version.universe_version_id,
            "name": result.version.name,
            "config_hash": result.version.config_hash,
            "included_count": len(result.included_members),
            "excluded_count": len(result.excluded_members),
            "diagnostics": result.diagnostics,
            "top_10_symbols": [m.symbol for m in result.included_members[:10]],
            "approved_by": getattr(args, "approved_by", None),
            "operator_identity_warning": None
            if getattr(args, "approved_by", None) or getattr(args, "dry_run", False)
            else "--approved-by is recommended for persisted candidate generation",
        }
    )
    return 0


def handle_candidate_inspect(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        version_id = getattr(args, "version_id", None)
        if version_id:
            version = deps.version_repository.get_by_version_id(version_id)
        else:
            candidates = deps.version_repository.get_candidate_versions(limit=1)
            version = candidates[0] if candidates else None

        print_header("Candidate Inspect")
        if version is None:
            print_json({"found": False})
            return 0

        included = deps.version_repository.get_included_members(version.universe_version_id)
        excluded_stmt_count = len(
            deps.version_repository.get_members(version.universe_version_id)
        ) - len(included)

        print_json(
            {
                "found": True,
                "universe_version_id": version.universe_version_id,
                "name": version.name,
                "status": version.status,
                "effective_from": version.effective_from,
                "config_hash": version.config_hash,
                "included_count": len(included),
                "excluded_count": excluded_stmt_count,
                "generation_metadata": version.generation_metadata_json,
                "top_10_symbols": [
                    {"symbol": m.symbol, "rank": m.rank, "score": m.score} for m in included[:10]
                ],
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_candidate_inspect_rejections(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        version_id = args.version_id
        all_members = deps.version_repository.get_members(version_id)
        rejected = [m for m in all_members if m.excluded_reason is not None]

        reason_filter = getattr(args, "reason", None)
        if reason_filter:
            rejected = [m for m in rejected if m.excluded_reason == reason_filter]

        from collections import Counter

        reason_summary = dict(Counter(m.excluded_reason for m in rejected))

        print_header("Candidate Inspect Rejections")
        print_json(
            {
                "universe_version_id": version_id,
                "rejected_count": len(rejected),
                "reason_summary": reason_summary,
                "rejections": [
                    {
                        "symbol": m.symbol,
                        "excluded_reason": m.excluded_reason,
                        "liquidity": m.liquidity_metrics_json,
                        "quality": m.quality_metrics_json,
                    }
                    for m in rejected[:50]
                ],
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_candidate_inspect_symbol(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        symbol = args.symbol.strip().upper()
        version_id = getattr(args, "version_id", None)

        if not version_id:
            candidates = deps.version_repository.get_candidate_versions(limit=1)
            version = candidates[0] if candidates else None
            version_id = version.universe_version_id if version else None

        print_header("Candidate Inspect Symbol")
        if not version_id:
            print_json({"symbol": symbol, "found": False, "reason": "no_candidate_version"})
            return 0

        all_members = deps.version_repository.get_members(version_id)
        match = next((m for m in all_members if m.symbol == symbol), None)

        if match is None:
            print_json(
                {
                    "symbol": symbol,
                    "version_id": version_id,
                    "found": False,
                    "included": False,
                    "excluded_reason": None,
                }
            )
        else:
            print_json(
                {
                    "symbol": symbol,
                    "version_id": version_id,
                    "found": True,
                    "included": match.excluded_reason is None,
                    "rank": match.rank,
                    "score": match.score,
                    "included_reason": match.included_reason,
                    "excluded_reason": match.excluded_reason,
                    "liquidity_metrics": match.liquidity_metrics_json,
                    "quality_metrics": match.quality_metrics_json,
                }
            )
        return 0
    finally:
        deps.session.close()


def handle_history(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from autonomous_trading_platform.storage.sor.models.universe_versions import (
        UniverseVersion as UniverseVersionModel,
    )

    session = get_session()
    try:
        timestamp = _resolve_timestamp(getattr(args, "timestamp", None))
        limit = getattr(args, "limit", 20)
        status_filter = getattr(args, "status", None)

        stmt = (
            select(UniverseVersionModel)
            .order_by(UniverseVersionModel.created_at.desc())
            .limit(limit)
        )
        if status_filter:
            stmt = stmt.where(UniverseVersionModel.status == status_filter)

        rows = list(session.execute(stmt).scalars().all())

        print_header("Universe History")
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "total_returned": len(rows),
                "status_filter": status_filter,
                "versions": [
                    {
                        "universe_version_id": r.universe_version_id,
                        "name": r.name,
                        "status": r.status,
                        "source": r.source,
                        "effective_from": r.effective_from.isoformat()
                        if r.effective_from
                        else None,
                        "effective_to": r.effective_to.isoformat() if r.effective_to else None,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "rebalance_reason": r.rebalance_reason,
                        "config_hash": r.config_hash,
                    }
                    for r in rows
                ],
            }
        )
        return 0
    finally:
        session.close()


def handle_export_version(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        version = deps.version_repository.get_by_version_id(args.version_id)
        print_header("Export Universe Version")
        if version is None:
            print_json({"found": False, "universe_version_id": args.version_id})
            return 1

        members = deps.version_repository.get_members(version.universe_version_id)
        payload = {
            "schema_version": 1,
            "version": _version_payload(version, members, include_members=False),
            "members": [_member_payload(member) for member in members],
        }
        _write_json_output(args.output, payload)
        print_json(
            {
                "status": "success",
                "output": str(Path(args.output)),
                "universe_version_id": version.universe_version_id,
                "member_count": len(members),
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_import_version(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        input_path = Path(args.input)
        if not input_path.exists():
            print_error(f"Input file not found: {input_path}")
            return 1
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        version_payload = payload.get("version") if isinstance(payload, dict) else None
        raw_members = payload.get("members") if isinstance(payload, dict) else None
        if not isinstance(version_payload, dict) or not isinstance(raw_members, list):
            raise ValueError("Expected JSON object with 'version' and 'members' fields.")

        symbols = [
            str(member["symbol"]).strip().upper()
            for member in raw_members
            if isinstance(member, dict)
            and member.get("symbol")
            and member.get("excluded_reason") is None
        ]
        symbols = _dedupe_symbols(symbols)
        if not symbols:
            raise ValueError("Export artifact contains no included symbols to import.")

        timestamp = _resolve_timestamp(args.timestamp)
        imported_name = version_payload.get("name") or timestamp.date().isoformat()
        name = args.name or f"imported_{imported_name}"
        version, members = deps.version_service.build_version(
            name=name,
            effective_from=timestamp,
            symbols=symbols,
            source=version_payload.get("source") or UniverseSource.CUSTOM,
            rebalance_reason="import_version",
        )
        validation = deps.validation_service.validate_version_row(version, members)

        print_header("Import Universe Version")
        if getattr(args, "dry_run", False):
            print_json(
                {
                    "dry_run": True,
                    "status": "preview",
                    "input": str(input_path),
                    "activate": getattr(args, "activate", False),
                    "validation_ok": validation.ok,
                    "validation_errors": validation.errors,
                    "universe_version_id": version.universe_version_id,
                    "name": version.name,
                    "symbol_count": len(members),
                    "symbols_preview": [m.symbol for m in members[:25]],
                    "approved_by": getattr(args, "approved_by", None),
                }
            )
            deps.session.rollback()
            return 0 if validation.ok else 1

        if not validation.ok:
            print_json(
                {
                    "dry_run": False,
                    "status": "invalid",
                    "validation_errors": validation.errors,
                }
            )
            return 1

        if getattr(args, "activate", False):
            deps.version_repository.retire_active_version(version.effective_from)
        deps.version_service.save_version(version, members)
        if getattr(args, "activate", False):
            deps.version_repository.activate_version(version.universe_version_id)
        deps.session.commit()

        print_json(
            {
                "dry_run": False,
                "status": "success",
                "activated": getattr(args, "activate", False),
                "universe_version_id": version.universe_version_id,
                "name": version.name,
                "symbol_count": len(members),
                "approved_by": getattr(args, "approved_by", None),
                "operator_identity_warning": None
                if getattr(args, "approved_by", None)
                else "--approved-by is recommended for imported universe mutations",
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_runtime_status(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.universe.services.universe_resolution_service import (
        NoActiveUniverseError,
        UniverseResolutionService,
    )

    deps = build_dependencies()
    try:
        timestamp = _resolve_timestamp(getattr(args, "timestamp", None))
        resolution_service = UniverseResolutionService(deps.version_repository)

        try:
            active_version = resolution_service.resolve_active(timestamp)
            members = resolution_service.resolve_active_members(timestamp)
            member_count = len(members)
            status = "ready"
            error = None
        except NoActiveUniverseError as exc:
            active_version = None
            members = []
            member_count = 0
            status = "no_active_universe"
            error = str(exc)

        print_header("Universe Runtime Status")
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "status": status,
                "error": error,
                "universe_version_id": (
                    active_version.universe_version_id if active_version else None
                ),
                "universe_name": active_version.name if active_version else None,
                "universe_source": active_version.source if active_version else None,
                "effective_from": (
                    active_version.effective_from.isoformat()
                    if active_version and active_version.effective_from
                    else None
                ),
                "effective_to": (
                    active_version.effective_to.isoformat()
                    if active_version and active_version.effective_to
                    else None
                ),
                "member_count": member_count,
                "symbols_preview": members[:25],
                "ready_for": {
                    "ingestion": status == "ready",
                    "feature_pipeline": status == "ready",
                    "trading_cycle": status == "ready",
                    "simulation": status == "ready",
                },
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_observability_status(args: argparse.Namespace) -> int:
    from pathlib import Path

    from autonomous_trading_platform.storage.sor.repositories.core.universe_rebalance_repository import (
        UniverseRebalanceRepository,
    )
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
        UniverseRotationRepository,
    )

    deps = build_dependencies()
    try:
        timestamp = _resolve_timestamp(getattr(args, "timestamp", None))
        active = deps.version_repository.get_active_version(timestamp)
        members = (
            deps.version_repository.get_included_members(active.universe_version_id)
            if active is not None
            else []
        )
        validation = deps.validation_service.validate_active(timestamp)
        latest_rotation = UniverseRotationRepository(deps.session).get_latest()
        latest_rebalance = UniverseRebalanceRepository(deps.session).get_latest_run()
        dashboard = Path("infra/observability/grafana/dashboards/universe-management.json")

        print_header("Universe Observability Status")
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "active_universe_version_id": active.universe_version_id if active else None,
                "active_universe_size": len(members),
                "validation_ok": validation.ok,
                "validation_errors": validation.errors,
                "latest_rotation_id": latest_rotation.rotation_id if latest_rotation else None,
                "latest_rebalance_run_id": (
                    latest_rebalance.rebalance_run_id if latest_rebalance else None
                ),
                "dashboard_present": dashboard.exists(),
                "metrics": [
                    "ratp_universe_candidate_count",
                    "ratp_universe_rejected_symbol_count",
                    "ratp_universe_ranking_duration_seconds",
                    "ratp_universe_rebalance_duration_seconds",
                    "ratp_universe_rotation_duration_seconds",
                    "ratp_universe_active_size",
                    "ratp_universe_rebalance_churn_pct",
                    "ratp_universe_invalid_member_count",
                    "ratp_universe_resolution_latency_seconds",
                ],
            }
        )
        return 0 if validation.ok else 1
    finally:
        deps.session.close()


def handle_propose_rebalance(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.universe.types import UniverseRebalanceConfig

    timestamp = _resolve_timestamp(args.timestamp)
    config = UniverseRebalanceConfig(
        target_universe_size=args.target_size,
        max_churn_pct=args.max_churn_pct,
        retain_until_rank_greater_than=args.retain_threshold,
        add_only_if_rank_less_than_or_equal=args.add_threshold,
        force_rebalance=args.force,
    )
    result = run_rebalance(
        candidate_version_id=getattr(args, "candidate_version_id", None),
        active_version_id=getattr(args, "active_version_id", None),
        config=config,
        name=getattr(args, "name", None),
        as_of=timestamp,
        dry_run=args.dry_run,
    )

    print_header("Propose Rebalance")
    print_json(
        {
            "dry_run": args.dry_run,
            "proposed_version_id": result.proposed_version.universe_version_id,
            "proposed_name": result.proposed_version.name,
            "proposed_status": result.proposed_version.status,
            "candidate_version_id": result.rebalance_run.candidate_universe_version_id,
            "previous_version_id": result.rebalance_run.previous_universe_version_id,
            "rebalance_run_id": result.rebalance_run.rebalance_run_id,
            "added_count": len(result.added),
            "removed_count": len(result.removed),
            "retained_count": len(result.retained),
            "churn_pct": result.churn_pct,
            "top_additions": result.added[:10],
            "top_removals": result.removed[:10],
            "skipped_adds": result.skipped_adds[:10],
            "skipped_removes": result.skipped_removes[:10],
            "audit_summary": result.audit_summary,
        }
    )
    return 0


def handle_rotate(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.universe.types import UniverseRebalanceConfig

    timestamp = _resolve_timestamp(args.timestamp)
    config = UniverseRebalanceConfig(
        target_universe_size=args.target_size,
        max_churn_pct=args.max_churn_pct,
        retain_until_rank_greater_than=args.retain_threshold,
        add_only_if_rank_less_than_or_equal=args.add_threshold,
        force_rebalance=args.force,
    )
    result = run_universe_rotation(
        candidate_version_id=getattr(args, "candidate_version_id", None),
        config=config,
        rotation_reason=getattr(args, "rotation_reason", None),
        force_rotation=args.force,
        approved_by=getattr(args, "approved_by", None),
        as_of=timestamp,
        skip_cadence_check=args.skip_cadence_check,
        dry_run=args.dry_run,
    )

    print_header("Universe Rotate")
    if result is None:
        print_json(
            {
                "status": "skipped",
                "reason": "cadence_check_failed",
                "timestamp": timestamp.isoformat(),
            }
        )
        return 0

    rec = result.rotation_record
    print_json(
        {
            "dry_run": args.dry_run,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
            "rotation_type": result.rotation_type,
            "rotation_id": rec.rotation_id,
            "status": rec.status,
            "new_universe_version_id": result.new_version.universe_version_id,
            "new_universe_name": result.new_version.name,
            "previous_universe_version_id": rec.previous_universe_version_id,
            "candidate_universe_version_id": rec.candidate_universe_version_id,
            "rebalance_run_id": rec.rebalance_run_id,
            "added_count": len(result.rebalance_result.added) if result.rebalance_result else 0,
            "removed_count": len(result.rebalance_result.removed) if result.rebalance_result else 0,
            "retained_count": len(result.rebalance_result.retained)
            if result.rebalance_result
            else 0,
            "churn_pct": rec.churn_pct,
            "force_rotation": rec.force_rotation,
            "rotation_reason": rec.rotation_reason,
        }
    )
    return 0


def handle_rollback(args: argparse.Namespace) -> int:
    timestamp = _resolve_timestamp(args.timestamp)
    result = run_universe_rollback(
        target_version_id=args.target_version_id,
        rollback_reason=args.reason,
        approved_by=getattr(args, "approved_by", None),
        as_of=timestamp,
        dry_run=args.dry_run,
    )

    rec = result.rotation_record
    print_header("Universe Rollback")
    print_json(
        {
            "dry_run": args.dry_run,
            "rotation_type": result.rotation_type,
            "rotation_id": rec.rotation_id,
            "status": rec.status,
            "new_universe_version_id": result.new_version.universe_version_id,
            "new_universe_name": result.new_version.name,
            "previous_universe_version_id": rec.previous_universe_version_id,
            "rollback_reason": rec.rollback_reason,
            "approved_by": rec.approved_by,
            "member_count": len(rec.retained_symbols_json or []),
        }
    )
    return 0


def handle_rotation_history(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
        UniverseRotationRepository,
    )

    session = get_session()
    try:
        limit = getattr(args, "limit", 20)
        repo = UniverseRotationRepository(session)
        records = repo.get_recent(limit=limit)

        print_header("Universe Rotation History")
        print_json(
            {
                "total_returned": len(records),
                "records": [
                    {
                        "rotation_id": r.rotation_id,
                        "rotation_type": r.rotation_type,
                        "status": r.status,
                        "new_universe_version_id": r.new_universe_version_id,
                        "previous_universe_version_id": r.previous_universe_version_id,
                        "candidate_universe_version_id": r.candidate_universe_version_id,
                        "churn_pct": r.churn_pct,
                        "force_rotation": r.force_rotation,
                        "rotation_reason": r.rotation_reason,
                        "rollback_reason": r.rollback_reason,
                        "approved_by": r.approved_by,
                        "rejection_reason": r.rejection_reason,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in records
                ],
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_rotation(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        record = universe_rotation_repository.UniverseRotationRepository(
            session
        ).get_by_rotation_id(args.rotation_id)
        print_header("Inspect Universe Rotation")
        if record is None:
            print_json({"found": False, "rotation_id": args.rotation_id})
            return 1

        print_json(_rotation_payload(record))
        return 0
    finally:
        session.close()


def handle_rebalance_history(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rebalance_repository import (
        UniverseRebalanceRepository,
    )

    session = get_session()
    try:
        repo = UniverseRebalanceRepository(session)
        runs = repo.get_recent(limit=getattr(args, "limit", 20))

        print_header("Universe Rebalance History")
        print_json(
            {
                "total_returned": len(runs),
                "records": [
                    {
                        "rebalance_run_id": r.rebalance_run_id,
                        "previous_universe_version_id": r.previous_universe_version_id,
                        "candidate_universe_version_id": r.candidate_universe_version_id,
                        "proposed_universe_version_id": r.proposed_universe_version_id,
                        "added_count": len(r.added_symbols_json or []),
                        "removed_count": len(r.removed_symbols_json or []),
                        "retained_count": len(r.retained_symbols_json or []),
                        "churn_pct": r.churn_pct,
                        "target_universe_size": r.target_universe_size,
                        "max_churn_pct": r.max_churn_pct,
                        "status": r.status,
                        "rejection_reason": r.rejection_reason,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in runs
                ],
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_rebalance(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        run = universe_rebalance_repository.UniverseRebalanceRepository(session).get_by_run_id(
            args.rebalance_run_id
        )
        print_header("Inspect Universe Rebalance")
        if run is None:
            print_json({"found": False, "rebalance_run_id": args.rebalance_run_id})
            return 1

        print_json(_rebalance_payload(run))
        return 0
    finally:
        session.close()


def handle_rotation_status(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
        UniverseRotationRepository,
    )

    session = get_session()
    try:
        repo = UniverseRotationRepository(session)
        record = repo.get_latest()

        print_header("Universe Rotation Status")
        if record is None:
            print_json({"found": False, "message": "No rotation records found."})
            return 0

        print_json(
            {
                "found": True,
                "rotation_id": record.rotation_id,
                "rotation_type": record.rotation_type,
                "status": record.status,
                "new_universe_version_id": record.new_universe_version_id,
                "previous_universe_version_id": record.previous_universe_version_id,
                "candidate_universe_version_id": record.candidate_universe_version_id,
                "churn_pct": record.churn_pct,
                "force_rotation": record.force_rotation,
                "rotation_reason": record.rotation_reason,
                "rollback_reason": record.rollback_reason,
                "approved_by": record.approved_by,
                "rejection_reason": record.rejection_reason,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "summary": record.summary_json,
            }
        )
        return 0
    finally:
        session.close()


def handle_history_for_date(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
        UniverseRotationRepository,
    )
    from autonomous_trading_platform.universe.services.universe_history_service import (
        UniverseHistoryService,
    )

    session = get_session()
    try:
        timestamp = _resolve_timestamp(args.timestamp)
        history_service = UniverseHistoryService(
            version_repo=UniverseVersionRepository(session),
            rotation_repo=UniverseRotationRepository(session),
        )
        version = history_service.get_active_as_of(timestamp)
        symbols = history_service.get_included_symbols_as_of(timestamp) if version else []

        print_header("Universe History for Date")
        print_json(
            {
                "timestamp": timestamp.isoformat(),
                "found": version is not None,
                "universe_version_id": version.universe_version_id if version else None,
                "name": version.name if version else None,
                "status": version.status if version else None,
                "source": version.source if version else None,
                "effective_from": version.effective_from.isoformat() if version else None,
                "effective_to": version.effective_to.isoformat()
                if version and version.effective_to
                else None,
                "config_hash": version.config_hash if version else None,
                "symbol_count": len(symbols),
                "symbols_preview": symbols[:25],
            }
        )
        return 0
    finally:
        session.close()


def handle_replay_timeline(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
        UniverseRotationRepository,
    )
    from autonomous_trading_platform.universe.services.universe_history_service import (
        UniverseHistoryService,
    )

    session = get_session()
    try:
        start_dt = parse_datetime(args.start)
        end_dt = parse_datetime(args.end)
        history_service = UniverseHistoryService(
            version_repo=UniverseVersionRepository(session),
            rotation_repo=UniverseRotationRepository(session),
        )
        transitions = history_service.build_replay_transitions(start_dt, end_dt)

        print_header("Universe Replay Timeline")
        print_json(
            {
                "window_start": start_dt.isoformat(),
                "window_end": end_dt.isoformat(),
                "transition_count": len(transitions),
                "transitions": [
                    {
                        "universe_version_id": t.universe_version_id,
                        "effective_from": t.effective_from.isoformat(),
                        "effective_to": t.effective_to.isoformat() if t.effective_to else None,
                        "added_count": len(t.added_symbols),
                        "removed_count": len(t.removed_symbols),
                        "retained_count": len(t.retained_symbols),
                        "added_symbols_preview": t.added_symbols[:10],
                        "removed_symbols_preview": t.removed_symbols[:10],
                    }
                    for t in transitions
                ],
            }
        )
        return 0
    finally:
        session.close()


def handle_compare_universes(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        repo = UniverseVersionRepository(session)
        ver_a = repo.get_by_version_id(args.version_a)
        ver_b = repo.get_by_version_id(args.version_b)

        print_header("Compare Universes")
        if ver_a is None or ver_b is None:
            missing = []
            if ver_a is None:
                missing.append(args.version_a)
            if ver_b is None:
                missing.append(args.version_b)
            print_json({"error": "version_not_found", "missing": missing})
            return 1

        syms_a = set(repo.get_included_symbols(ver_a.universe_version_id))
        syms_b = set(repo.get_included_symbols(ver_b.universe_version_id))

        only_in_a = sorted(syms_a - syms_b)
        only_in_b = sorted(syms_b - syms_a)
        in_both = sorted(syms_a & syms_b)

        payload = {
            "version_a": {
                "universe_version_id": ver_a.universe_version_id,
                "name": ver_a.name,
                "effective_from": ver_a.effective_from.isoformat(),
                "symbol_count": len(syms_a),
            },
            "version_b": {
                "universe_version_id": ver_b.universe_version_id,
                "name": ver_b.name,
                "effective_from": ver_b.effective_from.isoformat(),
                "symbol_count": len(syms_b),
            },
            "only_in_a_count": len(only_in_a),
            "only_in_b_count": len(only_in_b),
            "in_both_count": len(in_both),
            "churn_pct": round((len(only_in_a) + len(only_in_b)) / max(len(syms_a | syms_b), 1), 4),
            "only_in_a_preview": only_in_a[:20],
            "only_in_b_preview": only_in_b[:20],
            "only_in_a": only_in_a if getattr(args, "output", None) else None,
            "only_in_b": only_in_b if getattr(args, "output", None) else None,
        }
        _emit_payload(payload, args)
        return 0
    finally:
        session.close()


def handle_symbol_history(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from autonomous_trading_platform.storage.sor.models.universe_versions import (
        UniverseMember as UniverseMemberModel,
    )
    from autonomous_trading_platform.storage.sor.models.universe_versions import (
        UniverseVersion as UniverseVersionModel,
    )

    session = get_session()
    try:
        symbol = args.symbol.strip().upper()
        start_ts = parse_datetime(args.start) if args.start else None
        end_ts = parse_datetime(args.end) if args.end else None

        stmt = (
            select(UniverseVersionModel)
            .join(
                UniverseMemberModel,
                UniverseMemberModel.universe_version_id == UniverseVersionModel.universe_version_id,
            )
            .where(
                UniverseMemberModel.symbol == symbol,
                UniverseMemberModel.excluded_reason.is_(None),
            )
            .order_by(UniverseVersionModel.effective_from.asc())
        )
        if start_ts:
            stmt = stmt.where(UniverseVersionModel.effective_from >= start_ts)
        if end_ts:
            stmt = stmt.where(UniverseVersionModel.effective_from < end_ts)

        versions = list(session.execute(stmt).scalars().all())

        print_header("Symbol Universe History")
        print_json(
            {
                "symbol": symbol,
                "window_start": start_ts.isoformat() if start_ts else None,
                "window_end": end_ts.isoformat() if end_ts else None,
                "version_count": len(versions),
                "versions": [
                    {
                        "universe_version_id": v.universe_version_id,
                        "name": v.name,
                        "status": v.status,
                        "effective_from": v.effective_from.isoformat(),
                        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
                    }
                    for v in versions
                ],
            }
        )
        return 0
    finally:
        session.close()


def handle_lifecycle_symbol(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        symbol = args.symbol.strip().upper()
        timestamp = _resolve_timestamp(getattr(args, "timestamp", None))
        lifecycle_repo = TickerLifecycleRepository(session)
        lifecycle_service = TickerLifecycleService(lifecycle_repo)
        events = lifecycle_repo.list_events_for_symbol(symbol)

        from sqlalchemy import select

        from autonomous_trading_platform.storage.sor.models.universe_versions import (
            UniverseMember as UniverseMemberModel,
        )
        from autonomous_trading_platform.storage.sor.models.universe_versions import (
            UniverseVersion as UniverseVersionModel,
        )

        stmt = (
            select(UniverseVersionModel)
            .join(
                UniverseMemberModel,
                UniverseMemberModel.universe_version_id == UniverseVersionModel.universe_version_id,
            )
            .where(
                UniverseMemberModel.symbol == symbol,
                UniverseMemberModel.excluded_reason.is_(None),
                UniverseVersionModel.effective_from <= timestamp,
            )
            .order_by(UniverseVersionModel.effective_from.asc())
        )
        versions = list(session.execute(stmt).scalars().all())

        print_header("Lifecycle Symbol")
        print_json(
            {
                "symbol": symbol,
                "timestamp": timestamp.isoformat(),
                "resolved_symbol": lifecycle_service.resolve_symbol(symbol, timestamp),
                "delisted": lifecycle_service.is_delisted(symbol, timestamp),
                "event_count": len(events),
                "events": [
                    {
                        "event_id": event.event_id,
                        "symbol": event.symbol,
                        "event_type": str(event.event_type),
                        "effective_at": event.effective_at.isoformat(),
                        "successor_symbol": event.successor_symbol,
                        "source": event.source,
                        "notes": event.notes,
                    }
                    for event in events
                ],
                "membership_version_count": len(versions),
                "membership_versions": [
                    {
                        "universe_version_id": version.universe_version_id,
                        "name": version.name,
                        "status": version.status,
                        "effective_from": version.effective_from.isoformat(),
                        "effective_to": version.effective_to.isoformat()
                        if version.effective_to
                        else None,
                    }
                    for version in versions
                ],
            }
        )
        return 0
    finally:
        session.close()


def _parse_symbols(raw: str) -> list[str]:
    symbols = [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")
    return symbols


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for symbol in symbols:
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(symbol)
    return deduped


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _bounded_limit(raw: str) -> int:
    value = _positive_int(raw)
    if value > 1000:
        raise argparse.ArgumentTypeError("value must be <= 1000")
    return value


def _pct(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be numeric") from exc
    if value < 0 or value > 1:
        raise argparse.ArgumentTypeError("value must be between 0 and 1")
    return value


def _positive_decimal(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be decimal-compatible") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _emit_payload(payload: dict[str, Any], args: argparse.Namespace) -> None:
    output = getattr(args, "output", None)
    if output:
        _write_json_output(output, payload)
    print_json(payload)


def _write_json_output(output: str, payload: dict[str, Any]) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _version_payload(version: Any, members: list[Any], *, include_members: bool) -> dict[str, Any]:
    included = [member for member in members if member.excluded_reason is None]
    excluded = [member for member in members if member.excluded_reason is not None]
    payload: dict[str, Any] = {
        "universe_version_id": version.universe_version_id,
        "name": version.name,
        "status": version.status,
        "source": version.source,
        "effective_from": version.effective_from.isoformat() if version.effective_from else None,
        "effective_to": version.effective_to.isoformat() if version.effective_to else None,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "rebalance_reason": version.rebalance_reason,
        "config_hash": version.config_hash,
        "generation_metadata": version.generation_metadata_json,
        "member_count": len(members),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "symbols_preview": [member.symbol for member in included[:25]],
    }
    if include_members:
        payload["members"] = [_member_payload(member) for member in members]
    return payload


def _member_payload(member: Any) -> dict[str, Any]:
    return {
        "symbol": member.symbol,
        "rank": member.rank,
        "score": member.score,
        "included_reason": member.included_reason,
        "excluded_reason": member.excluded_reason,
        "liquidity_metrics": member.liquidity_metrics_json,
        "quality_metrics": member.quality_metrics_json,
    }


def _rebalance_payload(run: Any) -> dict[str, Any]:
    return {
        "found": True,
        "rebalance_run_id": run.rebalance_run_id,
        "previous_universe_version_id": run.previous_universe_version_id,
        "candidate_universe_version_id": run.candidate_universe_version_id,
        "proposed_universe_version_id": run.proposed_universe_version_id,
        "added_symbols": run.added_symbols_json or [],
        "removed_symbols": run.removed_symbols_json or [],
        "retained_symbols": run.retained_symbols_json or [],
        "added_count": len(run.added_symbols_json or []),
        "removed_count": len(run.removed_symbols_json or []),
        "retained_count": len(run.retained_symbols_json or []),
        "churn_pct": run.churn_pct,
        "target_universe_size": run.target_universe_size,
        "max_churn_pct": run.max_churn_pct,
        "config": run.config_json,
        "summary": run.summary_json,
        "status": run.status,
        "rejection_reason": run.rejection_reason,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _rotation_payload(record: Any) -> dict[str, Any]:
    return {
        "found": True,
        "rotation_id": record.rotation_id,
        "rotation_type": record.rotation_type,
        "status": record.status,
        "new_universe_version_id": record.new_universe_version_id,
        "previous_universe_version_id": record.previous_universe_version_id,
        "candidate_universe_version_id": record.candidate_universe_version_id,
        "rebalance_run_id": record.rebalance_run_id,
        "added_symbols": record.added_symbols_json or [],
        "removed_symbols": record.removed_symbols_json or [],
        "retained_symbols": record.retained_symbols_json or [],
        "churn_pct": record.churn_pct,
        "force_rotation": record.force_rotation,
        "rotation_reason": record.rotation_reason,
        "rollback_reason": record.rollback_reason,
        "approved_by": record.approved_by,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "approval_required": record.approval_required,
        "rejection_reason": record.rejection_reason,
        "summary": record.summary_json,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
