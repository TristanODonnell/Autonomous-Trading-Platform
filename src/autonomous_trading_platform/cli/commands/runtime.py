from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    build_trading_cycle_window,
)
from autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle import (
    run_strategy_allocation_rebalance_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_correlation_monitoring_cycle import (
    run_correlation_monitoring_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_drawdown_governance_ladder_cycle import (
    run_drawdown_governance_ladder_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_experiment_pipeline_cycle import (
    run_experiment_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_factor_exposure_monitoring_cycle import (
    run_factor_exposure_monitoring_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_factor_neutralization_verification_cycle import (
    run_factor_neutralization_verification_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_governance_demotion_cycle import (
    run_strategy_auto_demotion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_governance_promotion_cycle import (
    run_strategy_auto_promotion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_risk_budgeting_cycle import (
    run_risk_budgeting_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_strategy_health_lifecycle_cycle import (
    run_strategy_health_lifecycle_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.scheduler.cycles.run_trading_evaluation_cycle import (
    run_trading_evaluation_cycle,
)
from autonomous_trading_platform.scheduler.registry.manual_trigger_service import (
    ManualTriggerService,
)
from autonomous_trading_platform.scheduler.registry.scheduler_registry import SCHEDULER_REGISTRY
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

# All dispatchers that the CLI can trigger. Keyed by job_name in SCHEDULER_REGISTRY.
# Jobs with complex required arguments cannot be dispatched with simple defaults and are omitted.
_DISPATCHERS: dict[str, Callable[[], Any]] = {
    "trading_cycle": run_trading_cycle,
    "strategy_auto_promotion_cycle": lambda: run_strategy_auto_promotion_cycle(
        trigger_source="cli"
    ),
    "strategy_auto_demotion_cycle": lambda: run_strategy_auto_demotion_cycle(trigger_source="cli"),
    "strategy_allocation_rebalance_cycle": lambda: run_strategy_allocation_rebalance_cycle(
        trigger_source="cli"
    ),
    "market_ingestion_cycle": lambda: run_market_ingestion_cycle(
        trigger_type="manual", actor="cli"
    ),
    "feature_pipeline_cycle": run_feature_pipeline_cycle,
    "corporate_action_ingestion_cycle": lambda: run_corporate_action_ingestion_cycle(
        trigger_type="manual", actor="cli"
    ),
    "factor_exposure_monitoring_cycle": lambda: run_factor_exposure_monitoring_cycle(
        trigger_source="cli"
    ),
    "factor_neutralization_verification_cycle": lambda: (
        run_factor_neutralization_verification_cycle(trigger_source="cli")
    ),
    "experiment_pipeline_cycle": run_experiment_pipeline_cycle,
    "correlation_monitoring_cycle": lambda: run_correlation_monitoring_cycle(trigger_source="cli"),
    "risk_budgeting_cycle": lambda: run_risk_budgeting_cycle(trigger_source="cli"),
    "drawdown_governance_ladder_cycle": lambda: run_drawdown_governance_ladder_cycle(
        trigger_source="cli"
    ),
    "strategy_health_lifecycle_cycle": lambda: run_strategy_health_lifecycle_cycle(
        trigger_source="cli"
    ),
}


def register(subparsers) -> None:
    runtime_parser = subparsers.add_parser("runtime", help="Runtime cycle operations")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command", required=True)

    # ------------------------------------------------------------------
    # list-jobs  (P0 - new)
    # ------------------------------------------------------------------
    list_jobs_parser = runtime_subparsers.add_parser(
        "list-jobs",
        help="Print all scheduler registry jobs with cadence, lock key, and CLI trigger support.",
    )
    list_jobs_parser.add_argument("--json", action="store_true")
    list_jobs_parser.set_defaults(func=handle_list_jobs)

    # ------------------------------------------------------------------
    # plan-job  (P0 - new)
    # ------------------------------------------------------------------
    plan_job_parser = runtime_subparsers.add_parser(
        "plan-job",
        help="Validate a job name and show registry definition, dispatcher availability, and expected side effects.",
    )
    plan_job_parser.add_argument("--job-name", required=True)
    plan_job_parser.add_argument("--json", action="store_true")
    plan_job_parser.set_defaults(func=handle_plan_job)

    # ------------------------------------------------------------------
    # list-job-runs  (P0 - new)
    # ------------------------------------------------------------------
    list_job_runs_parser = runtime_subparsers.add_parser(
        "list-job-runs",
        help="Inspect recent runtime job runs for a named job.",
    )
    list_job_runs_parser.add_argument("--job-name", required=True)
    list_job_runs_parser.add_argument("--limit", type=int, default=20)
    list_job_runs_parser.add_argument("--json", action="store_true")
    list_job_runs_parser.set_defaults(func=handle_list_job_runs)

    # ------------------------------------------------------------------
    # inspect-job-run  (P0 - new)
    # ------------------------------------------------------------------
    inspect_job_run_parser = runtime_subparsers.add_parser(
        "inspect-job-run",
        help="Inspect one job run with child runs and step log.",
    )
    inspect_job_run_parser.add_argument("--job-run-id", required=True)
    inspect_job_run_parser.add_argument("--json", action="store_true")
    inspect_job_run_parser.set_defaults(func=handle_inspect_job_run)

    # ------------------------------------------------------------------
    # plan-cycle  (P0 - new)
    # ------------------------------------------------------------------
    plan_cycle_parser = runtime_subparsers.add_parser(
        "plan-cycle",
        help=(
            "Resolve the trading cycle window, active universe, controls state, "
            "and whether the cycle would be blocked. Read-only."
        ),
    )
    plan_cycle_parser.add_argument(
        "--timestamp",
        help="ISO 8601 UTC timestamp to plan against (default: now).",
    )
    plan_cycle_parser.add_argument("--json", action="store_true")
    plan_cycle_parser.set_defaults(func=handle_plan_cycle)

    # ------------------------------------------------------------------
    # run-cycle  (existing - fixed + --dry-run added)
    # ------------------------------------------------------------------
    run_cycle_parser = runtime_subparsers.add_parser(
        "run-cycle",
        help=(
            "[BROKER/RUNTIME] Run one trading cycle. "
            "Use --dry-run to resolve the window and controls without dispatching orders."
        ),
    )
    run_cycle_parser.add_argument(
        "--timestamp",
        help="ISO 8601 UTC timestamp for the cycle (default: now).",
    )
    run_cycle_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve window and controls without executing the cycle.",
    )
    run_cycle_parser.add_argument("--json", action="store_true")
    run_cycle_parser.set_defaults(func=handle_run_cycle)

    # ------------------------------------------------------------------
    # trigger-job  (existing - --dry-run + full dispatcher coverage)
    # ------------------------------------------------------------------
    trigger_job_parser = runtime_subparsers.add_parser(
        "trigger-job",
        help="Manually trigger a registered scheduler job with no-overlap locking.",
    )
    trigger_job_parser.add_argument(
        "--job-name",
        required=True,
        choices=sorted(SCHEDULER_REGISTRY.keys()),
    )
    trigger_job_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate registry and dispatcher availability without running the job.",
    )
    trigger_job_parser.add_argument("--json", action="store_true")
    trigger_job_parser.set_defaults(func=handle_trigger_job)

    # ------------------------------------------------------------------
    # inspect-manifest  (existing)
    # ------------------------------------------------------------------
    inspect_manifest_parser = runtime_subparsers.add_parser(
        "inspect-manifest",
        help="Inspect a run manifest",
    )
    inspect_manifest_parser.add_argument("--run-id", required=True)
    inspect_manifest_parser.set_defaults(func=handle_inspect_manifest)

    # ------------------------------------------------------------------
    # inspect-audit  (existing)
    # ------------------------------------------------------------------
    inspect_audit_parser = runtime_subparsers.add_parser(
        "inspect-audit",
        help="Inspect run audit data",
    )
    inspect_audit_parser.add_argument("--run-id", required=True)
    inspect_audit_parser.set_defaults(func=handle_inspect_audit)

    # ------------------------------------------------------------------
    # rescue-orphans  (P1 - new)
    # ------------------------------------------------------------------
    rescue_parser = runtime_subparsers.add_parser(
        "rescue-orphans",
        help=(
            "List or mark stale 'running' job records as failed. "
            "Use --dry-run to list without writing."
        ),
    )
    rescue_parser.add_argument(
        "--cutoff-minutes",
        type=int,
        default=30,
        help="Mark jobs started more than N minutes ago as orphaned (default: 30).",
    )
    rescue_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List orphaned jobs without marking them failed.",
    )
    rescue_parser.add_argument("--json", action="store_true")
    rescue_parser.set_defaults(func=handle_rescue_orphans)

    # ------------------------------------------------------------------
    # calendar-status  (P1 - new)
    # ------------------------------------------------------------------
    calendar_parser = runtime_subparsers.add_parser(
        "calendar-status",
        help="Show market phase, next open/close, and EOD eligibility for a given timestamp.",
    )
    calendar_parser.add_argument(
        "--timestamp",
        help="ISO 8601 UTC timestamp (default: now).",
    )
    calendar_parser.add_argument("--json", action="store_true")
    calendar_parser.set_defaults(func=handle_calendar_status)

    # ------------------------------------------------------------------
    # replay-plan  (P1 - new)
    # ------------------------------------------------------------------
    replay_plan_parser = runtime_subparsers.add_parser(
        "replay-plan",
        help="Validate replay symbols/date/cycles and summarize intended writes without running.",
    )
    _add_replay_arguments(replay_plan_parser)
    replay_plan_parser.add_argument("--json", action="store_true")
    replay_plan_parser.set_defaults(func=handle_replay_plan)

    # ------------------------------------------------------------------
    # replay  (existing)
    # ------------------------------------------------------------------
    replay_parser = runtime_subparsers.add_parser(
        "replay",
        help="Run replay runtime service and persist runtime_replay job evidence",
    )
    _add_replay_arguments(replay_parser)
    replay_parser.set_defaults(func=handle_replay)

    # ------------------------------------------------------------------
    # replay-debug  (existing)
    # ------------------------------------------------------------------
    replay_debug_parser = runtime_subparsers.add_parser(
        "replay-debug",
        help="Deterministic local runtime replay for settings/control wiring validation",
    )
    _add_replay_arguments(replay_debug_parser)
    replay_debug_parser.set_defaults(func=handle_replay_debug)

    # ------------------------------------------------------------------
    # replay-ingestion  (existing)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # evaluate-cycle  (existing)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # list-failed-runs  (existing)
    # ------------------------------------------------------------------
    list_failed_parser = runtime_subparsers.add_parser(
        "list-failed-runs",
        help="List recent failed run manifests (runtime-native alias of admin inspect-failed-runs)",
    )
    list_failed_parser.add_argument("--limit", type=int, default=25)
    list_failed_parser.set_defaults(func=handle_list_failed_runs)

    # ------------------------------------------------------------------
    # soak-loop  (existing)
    # ------------------------------------------------------------------
    soak_loop_parser = runtime_subparsers.add_parser(
        "soak-loop",
        help="Soak testing loop for paper trading or historical research",
    )
    soak_loop_subparsers = soak_loop_parser.add_subparsers(dest="soak_command", required=True)
    register_soak_loop_commands(soak_loop_subparsers)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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
    parser.add_argument("--calendar-mode", choices=["historical"], default="historical")
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


def _job_run_to_dict(row: Any) -> dict[str, Any]:
    return {
        "job_run_id": row.job_run_id,
        "job_name": row.job_name,
        "parent_job_run_id": row.parent_job_run_id,
        "status": row.status,
        "trigger_type": row.trigger_type,
        "started_at": str(row.started_at) if row.started_at else None,
        "completed_at": str(row.completed_at) if row.completed_at else None,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
        "correlation_id": row.correlation_id,
    }


def _step_to_dict(row: Any) -> dict[str, Any]:
    return {
        "step_id": str(row.step_id) if row.step_id else None,
        "step_name": row.step_name,
        "status": row.status,
        "sequence_number": row.sequence_number,
        "started_at": str(row.started_at) if row.started_at else None,
        "completed_at": str(row.completed_at) if row.completed_at else None,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
        "error_type": getattr(row, "error_type", None),
    }


# ---------------------------------------------------------------------------
# P0 new handlers
# ---------------------------------------------------------------------------


def handle_list_jobs(args: argparse.Namespace) -> int:
    jobs = [
        {
            "job_name": defn.job_name,
            "cron": defn.cron,
            "interval_seconds": defn.interval_seconds,
            "manual_trigger_enabled": defn.manual_trigger_enabled,
            "lock_key": defn.lock_key,
            "cli_dispatchable": defn.job_name in _DISPATCHERS,
        }
        for defn in sorted(SCHEDULER_REGISTRY.values(), key=lambda d: d.job_name)
    ]

    if getattr(args, "json", False):
        print_json({"count": len(jobs), "jobs": jobs})
    else:
        print_header("Scheduler Registry Jobs")
        print(f"  Total: {len(jobs)}")
        print()
        col = 46
        print(f"  {'job_name':<{col}} {'cron/interval':<26} dispatch  lock_key")
        print("  " + "-" * 110)
        for j in jobs:
            schedule = j["cron"] or (
                f"every {j['interval_seconds']}s" if j["interval_seconds"] else "manual"
            )
            dispatch = "YES" if j["cli_dispatchable"] else "no"
            print(f"  {j['job_name']:<{col}} {schedule:<26} {dispatch:<9} {j['lock_key']}")
    return 0


def handle_plan_job(args: argparse.Namespace) -> int:
    job_name: str = args.job_name

    if job_name not in SCHEDULER_REGISTRY:
        print_error(
            f"Job not found in registry: {job_name}. Run 'atp runtime list-jobs' to see all jobs."
        )
        return 1

    defn = SCHEDULER_REGISTRY[job_name]
    cli_dispatchable = job_name in _DISPATCHERS

    payload: dict[str, Any] = {
        "job_name": defn.job_name,
        "cron": defn.cron,
        "interval_seconds": defn.interval_seconds,
        "manual_trigger_enabled": defn.manual_trigger_enabled,
        "lock_key": defn.lock_key,
        "cli_dispatchable": cli_dispatchable,
        "trigger_command": (
            f"atp runtime trigger-job --job-name {job_name}" if cli_dispatchable else None
        ),
        "dry_run_command": (
            f"atp runtime trigger-job --job-name {job_name} --dry-run" if cli_dispatchable else None
        ),
        "side_effects_note": _job_side_effects_note(job_name),
        "lock_type": "InMemoryNoOverlapLock (single-process only)",
        "warning": (
            None
            if cli_dispatchable
            else "This job is not dispatchable via the CLI. It requires runtime parameters not available at the command line."
        ),
    }

    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Job Plan: {job_name}")
        for k, v in payload.items():
            if v is not None:
                print(f"  {k:<32} {v}")
    return 0


def _job_side_effects_note(job_name: str) -> str:
    notes = {
        "trading_cycle": "Reads universe/controls/settings; submits orders to broker; writes run manifests and audit logs.",
        "strategy_auto_promotion_cycle": "Mutates governance state; records promotion evidence; emits notification events.",
        "strategy_auto_demotion_cycle": "Mutates governance state; may disable strategies and zero allocations.",
        "strategy_allocation_rebalance_cycle": "Mutates allocation weights; writes rebalance run records.",
        "market_ingestion_cycle": "Fetches market bar data; writes raw Parquet dataset versions.",
        "feature_pipeline_cycle": "Computes features; writes feature Parquet dataset versions.",
        "corporate_action_ingestion_cycle": "Fetches corporate action data; writes to SoR.",
        "factor_exposure_monitoring_cycle": "Computes factor exposure snapshots; writes to SoR.",
        "factor_neutralization_verification_cycle": "Verifies factor neutralization; writes audit records.",
        "experiment_pipeline_cycle": "Runs research experiment pipeline; writes simulation artifacts.",
        "correlation_monitoring_cycle": "Computes correlation/covariance snapshots; writes to SoR.",
        "risk_budgeting_cycle": "Computes risk budget allocations; writes advisory snapshots to SoR.",
        "drawdown_governance_ladder_cycle": "Evaluates drawdown ladder; may escalate governance states.",
        "strategy_health_lifecycle_cycle": "Evaluates strategy health; may transition health states and penalties.",
    }
    return notes.get(job_name, "Unknown side effects.")


def handle_list_job_runs(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.db import get_session
    from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
        RuntimeJobRunRepository,
    )

    job_name: str = args.job_name
    limit: int = args.limit

    session = get_session()
    try:
        repo = RuntimeJobRunRepository(session)
        all_rows = repo.list_by_job_name(job_name=job_name)
        rows = all_rows[:limit]
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to list job runs: {exc}")
        return 1
    finally:
        session.close()

    payload = [_job_run_to_dict(r) for r in rows]
    if getattr(args, "json", False):
        print_json({"job_name": job_name, "total_returned": len(payload), "runs": payload})
    else:
        print_header(f"Job Runs: {job_name}")
        print(f"  Showing {len(payload)} of {len(all_rows)} total")
        print()
        for r in payload:
            dur = f"{r['duration_ms']}ms" if r["duration_ms"] else "?"
            print(
                f"  {r['job_run_id'][:36]:<38} {r['status']:<12} {str(r['started_at'])[:19]:<22} {dur}"
            )
    return 0


def handle_inspect_job_run(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.db import get_session
    from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
        RuntimeJobRunRepository,
    )
    from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_step_repository import (
        RuntimeJobRunStepRepository,
    )

    job_run_id: str = args.job_run_id

    session = get_session()
    try:
        run_repo = RuntimeJobRunRepository(session)
        step_repo = RuntimeJobRunStepRepository(session)

        row = run_repo.get_by_job_run_id(job_run_id)
        if row is None:
            print_error(f"Job run not found: {job_run_id}")
            return 1

        children = run_repo.list_children(parent_job_run_id=job_run_id)
        steps = step_repo.list_by_job_run_id(job_run_id=job_run_id)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to inspect job run: {exc}")
        return 1
    finally:
        session.close()

    job_run_payload = _job_run_to_dict(row)
    step_payloads = [_step_to_dict(s) for s in steps]
    child_payloads = [_job_run_to_dict(c) for c in children]
    payload = {
        "job_run": job_run_payload,
        "input_summary": getattr(row, "input_summary_json", None),
        "output_summary": getattr(row, "output_summary_json", None),
        "steps": step_payloads,
        "children": child_payloads,
    }
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Job Run: {job_run_id}")
        print(f"  job_name     : {job_run_payload['job_name']}")
        print(f"  status       : {job_run_payload['status']}")
        print(f"  started_at   : {job_run_payload['started_at']}")
        print(f"  completed_at : {job_run_payload['completed_at']}")
        print(f"  duration_ms  : {job_run_payload['duration_ms']}")
        if job_run_payload["error_message"]:
            print(f"  error        : {job_run_payload['error_message']}")
        if steps:
            print(f"\n  Steps ({len(steps)}):")
            for s in step_payloads:
                print(f"    [{s['sequence_number']:>3}] {s['step_name']:<40} {s['status']}")
        if children:
            print(f"\n  Child runs ({len(children)}):")
            for c in child_payloads:
                print(f"    {c['job_run_id'][:36]:<38} {c['job_name']:<32} {c['status']}")
    return 0


def handle_plan_cycle(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.db import get_session
    from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
        RuntimeControlStateRepository,
    )

    ts_str: str | None = getattr(args, "timestamp", None)
    try:
        now_utc = parse_datetime(ts_str) if ts_str else datetime.now(UTC)
    except Exception as exc:
        print_error(f"Invalid timestamp: {exc}")
        return 1

    try:
        window = build_trading_cycle_window(now_utc=now_utc)
    except Exception as exc:
        print_error(f"Failed to build trading cycle window: {exc}")
        return 1

    session = get_session()
    try:
        ctrl_repo = RuntimeControlStateRepository(session)
        ctrl_state = ctrl_repo.get_global_state()

        # Resolve trading universe
        universe_info: dict[str, Any] = {}
        try:
            from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
                resolve_trading_universe,
            )

            symbols, universe_version_id, universe_source, member_count = resolve_trading_universe(
                session, now_utc
            )
            universe_info = {
                "symbols": sorted(symbols),
                "symbol_count": len(symbols),
                "universe_version_id": universe_version_id,
                "universe_source": universe_source,
                "member_count": member_count,
                "status": "resolved",
            }
        except Exception as exc:
            universe_info = {"status": "error", "error": str(exc)}
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to read controls state: {exc}")
        return 1
    finally:
        session.close()

    # Derive block reason from control state
    block_reason: str | None = None
    if ctrl_state is not None:
        if ctrl_state.kill_switch_enabled:
            block_reason = "kill_switch_enabled"
        elif not ctrl_state.trading_enabled:
            block_reason = "trading_disabled"
        elif ctrl_state.trading_paused:
            block_reason = "trading_paused"

    payload: dict[str, Any] = {
        "plan_timestamp": now_utc.isoformat(),
        "cycle_window": {
            "cycle_start": window.cycle_start.isoformat(),
            "cycle_end": window.cycle_end.isoformat(),
            "ingestion_deadline": window.ingestion_deadline.isoformat(),
        },
        "controls": {
            "kill_switch_enabled": ctrl_state.kill_switch_enabled if ctrl_state else None,
            "trading_enabled": ctrl_state.trading_enabled if ctrl_state else None,
            "trading_paused": ctrl_state.trading_paused if ctrl_state else None,
            "trading_mode": ctrl_state.trading_mode if ctrl_state else None,
        },
        "block_reason": block_reason,
        "cycle_would_run": block_reason is None,
        "universe": universe_info,
    }

    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Cycle Plan: {now_utc.isoformat()}")
        print(f"  cycle_start       : {window.cycle_start.isoformat()}")
        print(f"  cycle_end         : {window.cycle_end.isoformat()}")
        print(f"  ingestion_deadline: {window.ingestion_deadline.isoformat()}")
        print()
        if ctrl_state:
            print(f"  kill_switch       : {ctrl_state.kill_switch_enabled}")
            print(f"  trading_enabled   : {ctrl_state.trading_enabled}")
            print(f"  trading_paused    : {ctrl_state.trading_paused}")
            print(f"  trading_mode      : {ctrl_state.trading_mode}")
        print()
        if block_reason:
            print(f"  [BLOCKED] {block_reason}")
        else:
            print("  [WOULD RUN]")
        print()
        if universe_info.get("status") == "resolved":
            print(
                f"  Universe: {universe_info['symbol_count']} symbols  source={universe_info['universe_source']}"
            )
        else:
            print(f"  Universe: {universe_info.get('error', 'unknown')}")
    return 0


# ---------------------------------------------------------------------------
# P1 new handlers
# ---------------------------------------------------------------------------


def handle_rescue_orphans(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.db import get_session
    from autonomous_trading_platform.runtime.services.orphan_job_recovery_service import (
        OrphanJobRecoveryService,
    )

    cutoff_minutes: int = args.cutoff_minutes
    dry_run: bool = args.dry_run
    cutoff = datetime.now(UTC) - timedelta(minutes=cutoff_minutes)
    orphan_payloads: list[dict[str, Any]] = []
    rescued_payloads: list[dict[str, Any]] = []

    session = get_session()
    try:
        if dry_run:
            # Read-only: list orphaned jobs without modifying them
            from sqlalchemy import select

            from autonomous_trading_platform.storage.sor.models.runtime_job_runs import (
                RuntimeJobRuns,
            )

            orphans = list(
                session.scalars(
                    select(RuntimeJobRuns)
                    .where(
                        RuntimeJobRuns.status == "running",
                        RuntimeJobRuns.started_at < cutoff,
                    )
                    .order_by(RuntimeJobRuns.started_at.asc())
                ).all()
            )
            orphan_payloads = [_job_run_to_dict(r) for r in orphans]
            payload = {
                "dry_run": True,
                "cutoff_minutes": cutoff_minutes,
                "cutoff_utc": cutoff.isoformat(),
                "orphan_count": len(orphans),
                "orphans": orphan_payloads,
            }
        else:
            service = OrphanJobRecoveryService(session)
            rescued = service.rescue_orphan_running_jobs(cutoff=cutoff)
            session.commit()
            rescued_payloads = [_job_run_to_dict(r) for r in rescued]
            payload = {
                "dry_run": False,
                "cutoff_minutes": cutoff_minutes,
                "cutoff_utc": cutoff.isoformat(),
                "rescued_count": len(rescued),
                "rescued": rescued_payloads,
            }
    except Exception as exc:
        session.rollback()
        print_error(f"Rescue orphans failed: {exc}")
        return 1
    finally:
        session.close()

    if getattr(args, "json", False):
        print_json(payload)
    else:
        action = "Found (dry-run)" if dry_run else "Rescued"
        count = len(orphan_payloads) if dry_run else len(rescued_payloads)
        rows = orphan_payloads if dry_run else rescued_payloads
        print_header(f"Rescue Orphans {'(dry-run)' if dry_run else ''}")
        print(f"  Cutoff     : {cutoff.isoformat()} ({cutoff_minutes} min ago)")
        print(f"  {action}: {count}")
        for r in rows:
            print(f"    {r['job_run_id'][:36]:<38} {r['job_name']:<36} started={r['started_at']}")
    return 0


def handle_calendar_status(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.runtime.clock import RealMarketCalendar

    ts_str: str | None = getattr(args, "timestamp", None)
    try:
        now_utc = parse_datetime(ts_str) if ts_str else datetime.now(UTC)
    except Exception as exc:
        print_error(f"Invalid timestamp: {exc}")
        return 1

    try:
        cal = RealMarketCalendar()
        state = cal.session_state(now_utc)
    except Exception as exc:
        print_error(f"Failed to compute calendar status: {exc}")
        return 1

    payload: dict[str, Any] = {
        "timestamp": now_utc.isoformat(),
        "market_phase": state.current_market_phase.value if state.current_market_phase else None,
        "is_trading_day": state.is_trading_day,
        "is_early_close": state.is_early_close,
        "eod_eligible": state.eod_eligible,
        "current_trading_date": str(state.current_trading_date)
        if state.current_trading_date
        else None,
        "next_market_open": state.next_market_open.isoformat() if state.next_market_open else None,
        "next_market_close": state.next_market_close.isoformat()
        if state.next_market_close
        else None,
        "calendar_source": state.calendar_source,
        "closure_reason": state.closure_reason,
    }

    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Calendar Status: {now_utc.isoformat()}")
        for k, v in payload.items():
            if v is not None:
                print(f"  {k:<28} {v}")
    return 0


def handle_replay_plan(args: argparse.Namespace) -> int:
    """Validate replay parameters and report intended writes without running."""
    try:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if not symbols:
            raise ValueError("At least one symbol must be provided")
        start_date = parse_datetime(args.start).date()
        end_date = parse_datetime(args.end).date()
        cycles = [c.strip() for c in args.cycles.split(",") if c.strip()]
    except Exception as exc:
        print_error(f"Invalid replay parameters: {exc}")
        return 1

    if start_date >= end_date:
        print_error(f"start ({start_date}) must be before end ({end_date})")
        return 1

    payload: dict[str, Any] = {
        "plan": True,
        "symbols": symbols,
        "start": str(start_date),
        "end": str(end_date),
        "starting_cash": str(args.starting_cash),
        "random_seed": args.random_seed,
        "price_basis": args.price_basis,
        "calendar_mode": args.calendar_mode,
        "cycles": cycles,
        "cadence_minutes": args.cadence_minutes,
        "max_ticks": args.max_ticks,
        "reset_sim_state": args.reset_sim_state,
        "days_span": (end_date - start_date).days,
        "estimated_ticks": _estimate_ticks(
            (end_date - start_date).days,
            args.cadence_minutes,
            args.max_ticks,
        ),
        "intended_writes": _replay_intended_writes(cycles),
        "note": "This is a plan only. Run 'atp runtime replay' or 'atp runtime replay-debug' to execute.",
    }

    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header("Replay Plan (read-only)")
        print(f"  symbols          : {', '.join(symbols)}")
        print(f"  date range       : {start_date} -> {end_date}  ({payload['days_span']} days)")
        print(f"  cycles           : {', '.join(cycles)}")
        print(f"  cadence_minutes  : {args.cadence_minutes}")
        print(f"  max_ticks        : {args.max_ticks or 'unlimited'}")
        print(f"  estimated_ticks  : {payload['estimated_ticks']}")
        print(f"  reset_sim_state  : {args.reset_sim_state}")
        print()
        print("  Intended writes:")
        for w in payload["intended_writes"]:
            print(f"    - {w}")
    return 0


def _estimate_ticks(days: int, cadence_minutes: int, max_ticks: int | None) -> int:
    trading_day_minutes = 390  # 6.5 hours
    ticks_per_day = max(1, trading_day_minutes // cadence_minutes)
    estimated = int(days * 5 / 7 * ticks_per_day)  # ~5/7 trading days
    if max_ticks is not None:
        return min(estimated, max_ticks)
    return estimated


def _replay_intended_writes(cycles: list[str]) -> list[str]:
    writes: list[str] = []
    cycle_writes = {
        "market_backfill": "Raw Parquet market bar dataset versions",
        "features": "Feature Parquet dataset versions",
        "trading": "Order records, fills, position/cash ledger entries, run manifests",
        "rebalance": "Allocation rebalance run records, allocation overrides",
        "portfolio_snapshot": "Portfolio snapshot records",
        "runtime_checks": "Runtime control state reads (no writes unless kill_switch triggers)",
        "full_runtime_day": "All of the above",
    }
    for c in cycles:
        desc = cycle_writes.get(c)
        if desc:
            writes.append(f"{c}: {desc}")
        else:
            writes.append(f"{c}: unknown cycle (no write profile)")
    if not writes:
        writes.append("No recognized cycles — nothing to write")
    return writes


# ---------------------------------------------------------------------------
# Existing handlers (fixed)
# ---------------------------------------------------------------------------


def handle_run_cycle(args: argparse.Namespace) -> int:
    dry_run: bool = getattr(args, "dry_run", False)
    ts_str: str | None = getattr(args, "timestamp", None)

    try:
        now_utc = parse_datetime(ts_str) if ts_str else datetime.now(UTC)
    except Exception as exc:
        print_error(f"Invalid timestamp: {exc}")
        return 1

    if dry_run:
        try:
            window = build_trading_cycle_window(now_utc=now_utc)
        except Exception as exc:
            print_error(f"Failed to build trading cycle window: {exc}")
            return 1
        payload = {
            "dry_run": True,
            "timestamp": now_utc.isoformat(),
            "cycle_start": window.cycle_start.isoformat(),
            "cycle_end": window.cycle_end.isoformat(),
            "ingestion_deadline": window.ingestion_deadline.isoformat(),
            "warning": (
                "Live run would call run_trading_cycle(), read broker account/positions, "
                "submit orders, write run manifests and audit logs."
            ),
        }
        if not getattr(args, "json", False):
            print_header("Run Cycle (dry-run)")
        print_json(payload)
        return 0

    print(
        "[runtime run-cycle] WARNING: This command runs the full trading cycle. "
        "It may submit orders. Use --dry-run to skip execution."
    )
    print_header("Run Trading Cycle")
    run_trading_cycle(now_utc=now_utc)
    print_json({"timestamp": now_utc.isoformat(), "status": "completed"})
    return 0


def handle_trigger_job(args: argparse.Namespace) -> int:
    job_name: str = args.job_name
    dry_run: bool = getattr(args, "dry_run", False)

    if job_name not in SCHEDULER_REGISTRY:
        print_error(f"Job not in registry: {job_name}")
        return 1

    defn = SCHEDULER_REGISTRY[job_name]
    if not defn.manual_trigger_enabled:
        print_error(f"Job '{job_name}' does not have manual_trigger_enabled=True.")
        return 1

    cli_dispatchable = job_name in _DISPATCHERS

    if dry_run:
        payload: dict[str, Any] = {
            "dry_run": True,
            "job_name": job_name,
            "manual_trigger_enabled": defn.manual_trigger_enabled,
            "lock_key": defn.lock_key,
            "cli_dispatchable": cli_dispatchable,
            "status": "would_trigger" if cli_dispatchable else "not_dispatchable",
            "side_effects_note": _job_side_effects_note(job_name),
        }
        if not cli_dispatchable:
            payload["warning"] = (
                f"'{job_name}' has no CLI dispatcher. It cannot be triggered from the CLI."
            )

        if getattr(args, "json", False):
            print_json(payload)
        else:
            print_header(f"Trigger Job Dry-run: {job_name}")
            for k, v in payload.items():
                if v is not None:
                    print(f"  {k:<28} {v}")
        return 0 if cli_dispatchable else 1

    result = ManualTriggerService(dispatchers=_DISPATCHERS).trigger(job_name)
    out = {
        "job_name": result.job_name,
        "status": result.status,
        "result": result.result,
    }
    if getattr(args, "json", False):
        print_json(out)
    else:
        print_header("Manual Scheduler Trigger")
        for k, v in out.items():
            print(f"  {k:<16} {v}")
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

    if args.run_trading:
        print(
            "[runtime replay-ingestion] WARNING: --run-trading elevates this from "
            "ingestion replay to trading-cycle execution. Orders may be submitted."
        )

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
