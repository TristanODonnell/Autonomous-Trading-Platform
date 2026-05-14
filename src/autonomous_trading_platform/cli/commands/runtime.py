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

    replay_debug_parser = runtime_subparsers.add_parser(
        "replay-debug",
        help="Deterministic local runtime replay for settings/control wiring validation",
    )
    replay_debug_parser.add_argument("--symbols", required=True)
    replay_debug_parser.add_argument("--start", required=True)
    replay_debug_parser.add_argument("--end", required=True)
    replay_debug_parser.add_argument("--starting-cash", type=Decimal, default=Decimal("10000"))
    replay_debug_parser.add_argument("--random-seed", type=int, default=42)
    replay_debug_parser.add_argument(
        "--price-basis",
        choices=[basis.value for basis in PriceBasis],
        default=PriceBasis.ADJUSTED.value,
    )
    replay_debug_parser.add_argument(
        "--calendar-mode",
        choices=["historical"],
        default="historical",
    )
    replay_debug_parser.add_argument(
        "--cycles",
        default="market_backfill,features,trading,rebalance,portfolio_snapshot",
        help="Comma-separated cycle names or full_runtime_day",
    )
    replay_debug_parser.add_argument("--reset-sim-state", action="store_true")
    replay_debug_parser.add_argument("--print-summary", action="store_true")
    replay_debug_parser.add_argument("--output-json", type=Path)
    replay_debug_parser.add_argument("--cadence-minutes", type=int, default=390)
    replay_debug_parser.add_argument("--max-ticks", type=int)
    replay_debug_parser.set_defaults(func=handle_replay_debug)


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
