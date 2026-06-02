from __future__ import annotations

import argparse
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from autonomous_trading_platform.cli.commands.runtime_soak_loop import register_soak_loop_commands
from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.runtime.replay_debug import (
    RuntimeReplayDebugRunner,
    RuntimeReplayInputs,
    format_text_summary,
)
from autonomous_trading_platform.runtime.services.replay_runtime_service import ReplayRuntimeService
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_dependencies,
)
from autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle import (
    run_strategy_allocation_rebalance_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_governance_demotion_cycle import (
    run_strategy_auto_demotion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_governance_promotion_cycle import (
    run_strategy_auto_promotion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.scheduler.cycles.run_trading_evaluation_cycle import (
    run_trading_evaluation_cycle,
)
from autonomous_trading_platform.scheduler.registry.manual_trigger_service import (
    ManualTriggerService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def register(subparsers) -> None:
    runtime_parser = subparsers.add_parser("runtime", help="Runtime cycle operations")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command", required=True)

    run_cycle_parser = runtime_subparsers.add_parser(
        "run-cycle",
        help="Run one trading cycle",
    )
    run_cycle_parser.add_argument("--timestamp")
    run_cycle_parser.set_defaults(func=handle_run_cycle)

    trigger_job_parser = runtime_subparsers.add_parser(
        "trigger-job",
        help="Manually trigger a registered scheduler job with no-overlap locking.",
    )
    trigger_job_parser.add_argument("--job-name", required=True)
    trigger_job_parser.set_defaults(func=handle_trigger_job)

    inspect_manifest_parser = runtime_subparsers.add_parser(
        "inspect-manifest",
        help="Inspect a run manifest",
    )
    inspect_manifest_parser.add_argument("--run-id", required=True)
    inspect_manifest_parser.set_defaults(func=handle_inspect_manifest)

    inspect_audit_parser = runtime_subparsers.add_parser(
        "inspect-audit",
        help="Inspect run audit data",
    )
    inspect_audit_parser.add_argument("--run-id", required=True)
    inspect_audit_parser.set_defaults(func=handle_inspect_audit)

    soak_loop_parser = runtime_subparsers.add_parser(
        "soak-loop",
        help="Soak testing loop for paper trading or historical research",
    )
    soak_loop_subparsers = soak_loop_parser.add_subparsers(dest="soak_command", required=True)
    register_soak_loop_commands(soak_loop_subparsers)

    replay_parser = runtime_subparsers.add_parser(
        "replay",
        help="Run replay runtime service and persist runtime_replay job evidence",
    )
    _add_replay_arguments(replay_parser)
    replay_parser.set_defaults(func=handle_replay)

    replay_debug_parser = runtime_subparsers.add_parser(
        "replay-debug",
        help="Deterministic local runtime replay for settings/control wiring validation",
    )
    _add_replay_arguments(replay_debug_parser)
    replay_debug_parser.set_defaults(func=handle_replay_debug)

    replay_ingestion_parser = runtime_subparsers.add_parser(
        "replay-ingestion",
        help="Replay historical ingestion tick-by-tick using real market data cycles",
    )
    replay_ingestion_parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    replay_ingestion_parser.add_argument(
        "--start", required=True, help="Start datetime (ISO 8601 UTC)"
    )
    replay_ingestion_parser.add_argument("--end", required=True, help="End datetime (ISO 8601 UTC)")
    replay_ingestion_parser.add_argument("--cadence-minutes", type=int, default=5)
    replay_ingestion_parser.add_argument(
        "--include-non-market-hours", action="store_true", default=False
    )
    replay_ingestion_parser.add_argument("--session-open-buffer-minutes", type=int, default=0)
    replay_ingestion_parser.add_argument("--session-close-buffer-minutes", type=int, default=0)
    replay_ingestion_parser.add_argument("--max-ticks", type=int, default=None)
    replay_ingestion_parser.add_argument("--run-trading", action="store_true", default=False)
    replay_ingestion_parser.add_argument("--stop-on-failure", action="store_true", default=False)
    replay_ingestion_parser.add_argument("--print-summary", action="store_true", default=False)
    replay_ingestion_parser.add_argument("--output-json", type=Path, default=None)
    replay_ingestion_parser.set_defaults(func=handle_replay_ingestion)

    # ── evaluate-cycle ────────────────────────────────────────────────────────
    evaluate_cycle_parser = runtime_subparsers.add_parser(
        "evaluate-cycle",
        help=(
            "[BROKER/RUNTIME] Run the full trading evaluation cycle for one bar timestamp. "
            "Reads broker account/positions/trades and writes signals, checkpoints, "
            "run manifests, and runtime state."
        ),
    )
    evaluate_cycle_parser.add_argument(
        "--timestamp",
        required=True,
        metavar="ISO8601",
        help="Bar timestamp to evaluate (e.g. 2026-05-26T15:35:00Z)",
    )
    evaluate_cycle_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print execution plan without running the cycle or touching broker APIs",
    )
    evaluate_cycle_parser.set_defaults(func=handle_evaluate_cycle)

    # ── list-failed-runs (Section 2 runtime-native wrapper) ──────────────────
    list_failed_parser = runtime_subparsers.add_parser(
        "list-failed-runs",
        help="List recent failed run manifests (runtime-native alias of admin inspect-failed-runs)",
    )
    list_failed_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Number of results to return (1–1000)",
    )
    list_failed_parser.set_defaults(func=handle_list_failed_runs)


def _add_replay_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--starting-cash", type=Decimal, default=Decimal("10000"))
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument(
        "--price-basis",
        choices=[basis.value for basis in PriceBasis],
        default=PriceBasis.ADJUSTED.value,
    )
    parser.add_argument(
        "--calendar-mode",
        choices=["historical"],
        default="historical",
    )
    parser.add_argument(
        "--cycles",
        default="market_backfill,features,trading,rebalance,portfolio_snapshot",
        help="Comma-separated cycle names or full_runtime_day",
    )
    parser.add_argument("--reset-sim-state", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--cadence-minutes", type=int, default=390)
    parser.add_argument("--max-ticks", type=int)


def handle_run_cycle(args: argparse.Namespace) -> int:
    print_header("Run Trading Cycle")
    run_trading_cycle()
    print_json({"status": "completed"})
    return 0


def handle_trigger_job(args: argparse.Namespace) -> int:
    dispatchers: dict[str, Callable[[], Any]] = {
        "trading_cycle": run_trading_cycle,
        "strategy_auto_promotion_cycle": lambda: run_strategy_auto_promotion_cycle(
            trigger_source="cli"
        ),
        "strategy_auto_demotion_cycle": lambda: run_strategy_auto_demotion_cycle(
            trigger_source="cli"
        ),
        "strategy_allocation_rebalance_cycle": lambda: run_strategy_allocation_rebalance_cycle(
            trigger_source="cli"
        ),
    }
    result = ManualTriggerService(dispatchers=dispatchers).trigger(args.job_name)
    print_header("Manual Scheduler Trigger")
    print_json(
        {
            "job_name": result.job_name,
            "status": result.status,
            "result": result.result,
        }
    )
    return 0 if result.status in {"completed", "skipped"} else 1


def handle_inspect_manifest(args: argparse.Namespace) -> int:
    print_header("Inspect Manifest")
    deps = build_trading_cycle_dependencies()
    session = deps.session
    try:
        with SorUnitOfWork(session) as uow:
            manifest = uow.run_manifests.get_by_run_id(args.run_id)

        print_json(
            {
                "run_id": args.run_id,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_audit(args: argparse.Namespace) -> int:
    print_header("Inspect Audit")
    deps = build_trading_cycle_dependencies()
    session = deps.session
    try:
        with SorUnitOfWork(session) as uow:
            audit_logs = uow.audit_logs.list_by_run_id(args.run_id)

        print_json(
            {
                "run_id": args.run_id,
                "audit_logs": [log.model_dump(mode="json") for log in audit_logs],
            }
        )
        return 0
    finally:
        session.close()


def handle_replay_debug(args: argparse.Namespace) -> int:
    print_header("Deterministic Local Runtime Replay")
    print(
        "This runner reads persisted platform settings/control state and never submits broker orders."
    )

    try:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if not symbols:
            raise ValueError("At least one symbol must be provided")
        cycles = [c.strip() for c in args.cycles.split(",") if c.strip()]
        inputs = RuntimeReplayInputs(
            symbols=symbols,
            start_date=parse_datetime(args.start).date(),
            end_date=parse_datetime(args.end).date(),
            starting_cash=args.starting_cash,
            random_seed=args.random_seed,
            price_basis=PriceBasis(args.price_basis),
            calendar_mode=args.calendar_mode,
            cycles=cycles,
            reset_sim_state=args.reset_sim_state,
            print_summary=args.print_summary,
            output_json=args.output_json,
            cadence_minutes=args.cadence_minutes,
            max_ticks=args.max_ticks,
        )
        summary = RuntimeReplayDebugRunner(inputs=inputs).run()
    except Exception as exc:
        print_error(f"Runtime replay-debug failed: {exc}")
        return 1

    if args.print_summary:
        print()
        print(format_text_summary(summary))
    else:
        print_json(
            {
                "status": "completed",
                "replay_id": summary["replay_id"],
                "settings_snapshot_hash": summary["settings_snapshot_hash"],
                "execution": summary["execution"],
                "trading": summary["trading"],
                "portfolio": summary["portfolio"],
                "warnings": summary["warnings"],
            }
        )
    return 0


def handle_replay(args: argparse.Namespace) -> int:
    print_header("Replay Runtime")
    try:
        inputs = _runtime_replay_inputs_from_args(args)
        summary = ReplayRuntimeService().run(inputs)
    except Exception as exc:
        print_error(f"Runtime replay failed: {exc}")
        return 1

    if args.print_summary:
        print()
        print(format_text_summary(summary))
    else:
        print_json(
            {
                "status": "completed",
                "replay_id": summary["replay_id"],
                "settings_snapshot_hash": summary["settings_snapshot_hash"],
                "execution": summary["execution"],
                "trading": summary["trading"],
                "portfolio": summary["portfolio"],
                "warnings": summary["warnings"],
            }
        )
    return 0


def _runtime_replay_inputs_from_args(args: argparse.Namespace) -> RuntimeReplayInputs:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")
    cycles = [c.strip() for c in args.cycles.split(",") if c.strip()]
    return RuntimeReplayInputs(
        symbols=symbols,
        start_date=parse_datetime(args.start).date(),
        end_date=parse_datetime(args.end).date(),
        starting_cash=args.starting_cash,
        random_seed=args.random_seed,
        price_basis=PriceBasis(args.price_basis),
        calendar_mode=args.calendar_mode,
        cycles=cycles,
        reset_sim_state=args.reset_sim_state,
        print_summary=args.print_summary,
        output_json=args.output_json,
        cadence_minutes=args.cadence_minutes,
        max_ticks=args.max_ticks,
    )


def handle_replay_ingestion(args: argparse.Namespace) -> int:
    import json

    from autonomous_trading_platform.db import get_session
    from autonomous_trading_platform.scheduler.orchestration.historical_ingestion_replay_orchestrator import (
        HistoricalIngestionReplayOrchestrator,
    )

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print_error("At least one symbol must be provided via --symbols")
        return 1

    try:
        start = parse_datetime(args.start)
        end = parse_datetime(args.end)
    except Exception as exc:
        print_error(f"Invalid datetime: {exc}")
        return 1

    print_header("Historical Ingestion Replay")
    print(f"  Symbols:          {', '.join(symbols)}")
    print(f"  Start:            {start.isoformat()}")
    print(f"  End:              {end.isoformat()}")
    print(f"  Cadence:          {args.cadence_minutes} min")
    print(f"  Market hours only:{not args.include_non_market_hours}")
    if args.max_ticks is not None:
        print(f"  Max ticks:        {args.max_ticks}")
    if args.run_trading:
        print("  Trading:          ENABLED")
    else:
        print("  Trading:          disabled (pass --run-trading to enable)")
    print()

    session = get_session()
    try:
        orchestrator = HistoricalIngestionReplayOrchestrator(session)
        result = orchestrator.run(
            symbols=symbols,
            start=start,
            end=end,
            cadence_minutes=args.cadence_minutes,
            market_hours_only=not args.include_non_market_hours,
            session_open_buffer_minutes=args.session_open_buffer_minutes,
            session_close_buffer_minutes=args.session_close_buffer_minutes,
            max_ticks=args.max_ticks,
            run_trading=args.run_trading,
            stop_on_failure=args.stop_on_failure,
        )
    except Exception as exc:
        print_error(f"Replay ingestion failed: {exc}")
        return 1
    finally:
        session.close()

    summary = {
        "replay_id": result.replay_id,
        "correlation_id": result.correlation_id,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "cadence_minutes": result.cadence_minutes,
        "symbols": result.symbols,
        "ticks_attempted": result.ticks_attempted,
        "ticks_ingestion_ok": result.ticks_ingestion_ok,
        "ticks_features_ok": result.ticks_features_ok,
        "ticks_trading_ok": result.ticks_trading_ok,
        "ticks_failed": result.ticks_failed,
        "ticks_skipped": result.ticks_skipped,
        "latest_raw_dataset_version_id": result.latest_raw_dataset_version_id,
        "warnings": result.warnings,
    }

    if result.ticks_failed > 0:
        print("Failed ticks:")
        for tick in result.tick_results:
            if tick.error:
                print(f"  {tick.tick_utc.isoformat()}  {tick.error}")
        print()

    if args.output_json is not None:
        args.output_json.write_text(json.dumps(summary, indent=2))

    print_json(summary)

    return 0 if result.ticks_ingestion_ok > 0 else 1


def handle_evaluate_cycle(args: argparse.Namespace) -> int:
    timestamp = parse_datetime(args.timestamp)

    if args.dry_run:
        print_header("Evaluate Cycle (dry-run)")
        print_json(
            {
                "dry_run": True,
                "timestamp": args.timestamp,
                "warning": (
                    "Live run would call run_trading_evaluation_cycle(), "
                    "read broker account/positions/trades, write signals, "
                    "checkpoints, run manifests, and runtime state."
                ),
            }
        )
        return 0

    print(
        "[runtime evaluate-cycle] WARNING: This command calls the full trading "
        "evaluation cycle. It will read broker account, positions, and latest "
        "trades, and will write signals, checkpoints, run manifests, and "
        "runtime state. Use --dry-run to skip execution."
    )

    run_trading_evaluation_cycle(timestamp=timestamp)

    print_header("Evaluate Cycle")
    print_json({"timestamp": args.timestamp, "status": "success"})
    return 0


_LIST_FAILED_LIMIT_MIN = 1
_LIST_FAILED_LIMIT_MAX = 1000


def handle_list_failed_runs(args: argparse.Namespace) -> int:
    """Runtime-native alias of `admin inspect-failed-runs`. No broker dependency."""
    from dotenv import load_dotenv

    from autonomous_trading_platform.db import get_session as _get_session
    from autonomous_trading_platform.storage.sor.services.unit_of_work import (
        SorUnitOfWork as _SorUnitOfWork,
    )

    limit = args.limit
    if not (_LIST_FAILED_LIMIT_MIN <= limit <= _LIST_FAILED_LIMIT_MAX):
        print_error(
            f"--limit must be between {_LIST_FAILED_LIMIT_MIN} and {_LIST_FAILED_LIMIT_MAX}, got {limit}"
        )
        return 1

    load_dotenv()
    session = _get_session()
    try:
        with _SorUnitOfWork(session) as uow:
            failed_runs = uow.run_manifests.list_failed_runs(limit=limit)

        print_header("Failed Runs")
        print_json(
            {
                "count": len(failed_runs),
                "runs": [
                    {
                        "run_id": str(row.run_id),
                        "run_type": row.run_type.value if row.run_type else None,
                        "status": row.status,
                        "bar_timestamp": (
                            row.bar_timestamp.isoformat() if row.bar_timestamp is not None else None
                        ),
                        "current_step": row.current_step,
                        "last_successful_step": row.last_successful_step,
                        "error_message": row.error_message,
                    }
                    for row in failed_runs
                ],
            }
        )
        return 0

    finally:
        session.close()
