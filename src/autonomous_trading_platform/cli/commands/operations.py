from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import (
    print_error,
    print_header,
    print_json,
    print_success,
)
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakStatus,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.observability.verification.runtime_soak_verification_service import (
    RuntimeSoakVerificationService,
)
from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
    check_ingestion_readiness_job,
)
from autonomous_trading_platform.storage.sor.models.operational_alerts import OperationalAlert


@dataclass
class OperationsCliDependencies:
    session: Session
    settings: Settings


def build_dependencies() -> OperationsCliDependencies:
    return OperationsCliDependencies(
        session=get_session(),
        settings=Settings(),
    )


_STALE_MINUTES_MIN = 1
_STALE_MINUTES_MAX = 1440

_RUNBOOKS_DIR = Path("docs/operations/runbooks")


# ---------------------------------------------------------------------------
# argparse type helpers
# ---------------------------------------------------------------------------


def _parse_window_arg(value: str) -> datetime:
    try:
        return parse_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _positive_stale_minutes(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--stale-after-minutes must be an integer") from exc
    if n < _STALE_MINUTES_MIN or n > _STALE_MINUTES_MAX:
        raise argparse.ArgumentTypeError(
            f"--stale-after-minutes must be between {_STALE_MINUTES_MIN} and {_STALE_MINUTES_MAX}"
        )
    return n


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    operations_parser = subparsers.add_parser("operations", help="Operations commands")
    operations_subparsers = operations_parser.add_subparsers(
        dest="operations_command",
        required=True,
    )

    # --- verify-runtime-soak ---
    verify_runtime_soak_parser = operations_subparsers.add_parser(
        "verify-runtime-soak",
        help="Run runtime soak verification checks",
    )
    verify_runtime_soak_parser.add_argument(
        "--window-start",
        required=True,
        type=_parse_window_arg,
        metavar="ISO8601",
        help="Start of soak window (e.g. 2026-05-08T12:00:00Z)",
    )
    verify_runtime_soak_parser.add_argument(
        "--window-end",
        required=True,
        type=_parse_window_arg,
        metavar="ISO8601",
        help="End of soak window (e.g. 2026-05-08T12:30:00Z)",
    )
    verify_runtime_soak_parser.add_argument(
        "--stale-after-minutes",
        type=_positive_stale_minutes,
        default=15,
        metavar="MINUTES",
        help=f"Minutes before a record is considered stale (1–{_STALE_MINUTES_MAX}; default: 15)",
    )
    verify_runtime_soak_parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Run verifier without writing to runtime_soak_reports (read-only mode).",
    )
    verify_runtime_soak_parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write full report JSON to this file path.",
    )
    verify_runtime_soak_parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit with code 1 for WARNING status (default: WARNING exits 0).",
    )
    verify_runtime_soak_parser.set_defaults(func=handle_verify_runtime_soak)

    # --- health ---
    health_parser = operations_subparsers.add_parser(
        "health",
        help="Print lightweight system health summary.",
    )
    health_parser.set_defaults(func=handle_health)

    # --- health-detailed ---
    health_detailed_parser = operations_subparsers.add_parser(
        "health-detailed",
        help="Print detailed service health (OTEL, jobs, data pipeline, market session, broker, controls).",
    )
    health_detailed_parser.set_defaults(func=handle_health_detailed)

    # --- list-jobs ---
    list_jobs_parser = operations_subparsers.add_parser(
        "list-jobs",
        help="List runtime job summaries (latest status, duration, error, run count).",
    )
    list_jobs_parser.set_defaults(func=handle_list_jobs)

    # --- list-job-runs ---
    list_job_runs_parser = operations_subparsers.add_parser(
        "list-job-runs",
        help="Inspect recent runs for a specific runtime job.",
    )
    list_job_runs_parser.add_argument("--job-name", required=True, metavar="NAME")
    list_job_runs_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of runs to return (default: 20).",
    )
    list_job_runs_parser.set_defaults(func=handle_list_job_runs)

    # --- runtime-state ---
    runtime_state_parser = operations_subparsers.add_parser(
        "runtime-state",
        help="Show current runtime control state (trading enabled, kill switch, mode, etc.).",
    )
    runtime_state_parser.set_defaults(func=handle_runtime_state)

    # --- list-alerts ---
    list_alerts_parser = operations_subparsers.add_parser(
        "list-alerts",
        help="List operational alerts.",
    )
    list_alerts_parser.add_argument(
        "--status",
        metavar="STATUS",
        help="Filter by status: active, acknowledged, snoozed, resolved.",
    )
    list_alerts_parser.add_argument(
        "--severity",
        metavar="SEVERITY",
        help="Filter by severity: info, warning, critical.",
    )
    list_alerts_parser.add_argument(
        "--category",
        metavar="CATEGORY",
        help="Filter by category: runtime, broker, reconciliation, risk_governance, control_infra.",
    )
    list_alerts_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Maximum number of alerts to return (default: 50).",
    )
    list_alerts_parser.set_defaults(func=handle_list_alerts)

    # --- acknowledge-alert ---
    ack_parser = operations_subparsers.add_parser(
        "acknowledge-alert",
        help="Acknowledge an operational alert.",
    )
    ack_parser.add_argument("--alert-id", required=True, metavar="ID")
    ack_parser.add_argument("--actor", required=True, metavar="USER")
    ack_parser.add_argument("--note", metavar="TEXT")
    ack_parser.set_defaults(func=handle_acknowledge_alert)

    # --- resolve-alert ---
    resolve_parser = operations_subparsers.add_parser(
        "resolve-alert",
        help="Resolve an operational alert.",
    )
    resolve_parser.add_argument("--alert-id", required=True, metavar="ID")
    resolve_parser.add_argument("--actor", required=True, metavar="USER")
    resolve_parser.add_argument("--note", metavar="TEXT")
    resolve_parser.set_defaults(func=handle_resolve_alert)

    # --- snooze-alert ---
    snooze_parser = operations_subparsers.add_parser(
        "snooze-alert",
        help="Temporarily suppress an alert until a given ISO8601 time.",
    )
    snooze_parser.add_argument("--alert-id", required=True, metavar="ID")
    snooze_parser.add_argument("--actor", required=True, metavar="USER")
    snooze_parser.add_argument(
        "--until",
        required=True,
        type=_parse_window_arg,
        metavar="ISO8601",
        help="Suppress the alert until this time (e.g. 2026-06-02T08:00:00Z).",
    )
    snooze_parser.add_argument("--note", metavar="TEXT")
    snooze_parser.set_defaults(func=handle_snooze_alert)

    # --- unsnooze-alert ---
    unsnooze_parser = operations_subparsers.add_parser(
        "unsnooze-alert",
        help="Remove a snooze from an alert.",
    )
    unsnooze_parser.add_argument("--alert-id", required=True, metavar="ID")
    unsnooze_parser.add_argument("--actor", required=True, metavar="USER")
    unsnooze_parser.add_argument("--note", metavar="TEXT")
    unsnooze_parser.set_defaults(func=handle_unsnooze_alert)

    # --- add-alert-note ---
    note_parser = operations_subparsers.add_parser(
        "add-alert-note",
        help="Add an operator note to an operational alert.",
    )
    note_parser.add_argument("--alert-id", required=True, metavar="ID")
    note_parser.add_argument("--actor", required=True, metavar="USER")
    note_parser.add_argument("--note", required=True, metavar="TEXT")
    note_parser.set_defaults(func=handle_add_alert_note)

    # --- latest-soak-report ---
    latest_soak_parser = operations_subparsers.add_parser(
        "latest-soak-report",
        help="Show the latest persisted soak report for an environment.",
    )
    latest_soak_parser.add_argument(
        "--environment",
        default=os.getenv("TRADING_ENVIRONMENT", "paper"),
        metavar="ENV",
        help="Environment to query (default: $TRADING_ENVIRONMENT or 'paper').",
    )
    latest_soak_parser.set_defaults(func=handle_latest_soak_report)

    # --- runbook list / show ---
    runbook_parser = operations_subparsers.add_parser(
        "runbook",
        help="Runbook discovery commands.",
    )
    runbook_subparsers = runbook_parser.add_subparsers(
        dest="runbook_command",
        required=True,
    )

    runbook_list_parser = runbook_subparsers.add_parser(
        "list",
        help="List available operations runbooks.",
    )
    runbook_list_parser.set_defaults(func=handle_runbook_list)

    runbook_show_parser = runbook_subparsers.add_parser(
        "show",
        help="Print a specific runbook path and contents.",
    )
    runbook_show_parser.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="Runbook file stem to look up (e.g. README).",
    )
    runbook_show_parser.set_defaults(func=handle_runbook_show)

    # --- inspect-ingestion-readiness ---
    ingestion_readiness_parser = operations_subparsers.add_parser(
        "inspect-ingestion-readiness",
        help="Check whether the current cycle is past the ingestion deadline (read-only).",
    )
    ingestion_readiness_parser.add_argument(
        "--timestamp",
        metavar="ISO8601",
        help="Override the current time for the readiness check (e.g. 2026-05-26T15:35:00Z).",
    )
    ingestion_readiness_parser.set_defaults(func=handle_inspect_ingestion_readiness)

    # --- verify-notification-events ---
    verify_notify_parser = operations_subparsers.add_parser(
        "verify-notification-events",
        help=(
            "Trigger each notification channel and report whether the notify_* flags "
            "actually gate anything, or if events fire unconditionally / not at all. "
            "Artifacts are written to artifacts/backtesting/."
        ),
    )
    verify_notify_parser.add_argument(
        "--controls",
        required=True,
        type=Path,
        help="Path to controls YAML fixture (e.g. fixtures/controls.yaml)",
    )
    verify_notify_parser.add_argument(
        "--settings",
        required=True,
        type=Path,
        help="Path to settings YAML fixture (e.g. fixtures/settings.yaml)",
    )
    verify_notify_parser.set_defaults(func=handle_verify_notification_events)


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def handle_verify_runtime_soak(args: argparse.Namespace) -> int:
    if args.window_end < args.window_start:
        raise ValueError("--window-end must be greater than or equal to --window-start")

    deps = build_dependencies()
    session = deps.session
    settings = deps.settings
    try:
        service = RuntimeSoakVerificationService(
            session=session,
            environment=settings.trading_environment.value,
            stale_after=timedelta(minutes=args.stale_after_minutes),
            persist_report=not args.no_persist,
        )
        report = service.verify(
            window_start=args.window_start,
            window_end=args.window_end,
        )
        if not args.no_persist:
            session.commit()

        print_header("Verify Runtime Soak")
        data = report.model_dump(mode="json")
        print_json(data)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
            print_success(f"Report saved: {output_path}")

        if report.status == RuntimeSoakStatus.FAILED:
            return 1
        if args.fail_on_warning and report.status == RuntimeSoakStatus.WARNING:
            return 1
        return 0
    finally:
        session.close()


def handle_health(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.system_health_service import (
        SystemHealthService,
    )

    session = get_session()
    try:
        settings = Settings()
        result = SystemHealthService(session=session, settings=settings).get_health()
        print_header("System Health")
        print_json(result)
        return 0
    finally:
        session.close()


def handle_health_detailed(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.health.detailed_system_health_service import (
        DetailedSystemHealthService,
    )

    session = get_session()
    try:
        environment = os.getenv("RATP_ENVIRONMENT", os.getenv("TRADING_ENVIRONMENT", "unknown"))
        report = DetailedSystemHealthService(
            session=session,
            environment=environment,
        ).get_health()
        print_header("Detailed System Health")
        print_json(report.model_dump(mode="json"))
        return 0
    finally:
        session.close()


def handle_list_jobs(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operations_service import (
        OperationsService,
    )

    session = get_session()
    try:
        jobs = OperationsService(session=session).list_jobs()
        print_header("Runtime Jobs")
        print_json(jobs)
        return 0
    finally:
        session.close()


def handle_list_job_runs(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operations_service import (
        OperationsService,
    )

    session = get_session()
    try:
        runs = OperationsService(session=session).list_job_runs(
            job_name=args.job_name,
            limit=args.limit,
        )
        print_header(f"Job Runs: {args.job_name}")
        print_json(runs)
        return 0
    finally:
        session.close()


def handle_runtime_state(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operations_service import (
        OperationsService,
    )

    session = get_session()
    try:
        state = OperationsService(session=session).get_runtime_state()
        print_header("Runtime State")
        print_json(state)
        return 0
    finally:
        session.close()


def handle_list_alerts(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operational_alert_service import (
        OperationalAlertService,
    )

    session = get_session()
    try:
        alerts = OperationalAlertService(session=session).list_alerts(
            status=args.status,
            severity=args.severity,
            category=args.category,
            limit=args.limit,
        )
        print_header("Operational Alerts")
        print_json([_alert_to_dict(a) for a in alerts])
        return 0
    finally:
        session.close()


def handle_acknowledge_alert(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operational_alert_service import (
        OperationalAlertService,
    )

    session = get_session()
    try:
        alert = OperationalAlertService(session=session).acknowledge_alert(
            alert_id=args.alert_id,
            actor=args.actor,
            note=args.note,
        )
        print_header("Alert Acknowledged")
        print_json(_alert_to_dict(alert))
        return 0
    except LookupError as exc:
        print_error(str(exc))
        return 1
    finally:
        session.close()


def handle_resolve_alert(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operational_alert_service import (
        OperationalAlertService,
    )

    session = get_session()
    try:
        alert = OperationalAlertService(session=session).resolve_alert(
            alert_id=args.alert_id,
            actor=args.actor,
            note=args.note,
        )
        print_header("Alert Resolved")
        print_json(_alert_to_dict(alert))
        return 0
    except LookupError as exc:
        print_error(str(exc))
        return 1
    finally:
        session.close()


def handle_snooze_alert(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operational_alert_service import (
        OperationalAlertService,
    )

    session = get_session()
    try:
        alert = OperationalAlertService(session=session).snooze_alert(
            alert_id=args.alert_id,
            actor=args.actor,
            snoozed_until=args.until,
            note=args.note,
        )
        print_header("Alert Snoozed")
        print_json(_alert_to_dict(alert))
        return 0
    except (LookupError, ValueError) as exc:
        print_error(str(exc))
        return 1
    finally:
        session.close()


def handle_unsnooze_alert(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operational_alert_service import (
        OperationalAlertService,
    )

    session = get_session()
    try:
        alert = OperationalAlertService(session=session).unsnooze_alert(
            alert_id=args.alert_id,
            actor=args.actor,
            note=args.note,
        )
        print_header("Alert Unsnoozed")
        print_json(_alert_to_dict(alert))
        return 0
    except LookupError as exc:
        print_error(str(exc))
        return 1
    finally:
        session.close()


def handle_add_alert_note(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.operational_alert_service import (
        OperationalAlertService,
    )

    session = get_session()
    try:
        alert = OperationalAlertService(session=session).add_note(
            alert_id=args.alert_id,
            actor=args.actor,
            note=args.note,
        )
        print_header("Note Added")
        print_json(_alert_to_dict(alert))
        return 0
    except LookupError as exc:
        print_error(str(exc))
        return 1
    finally:
        session.close()


def handle_latest_soak_report(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.storage.sor.repositories.core.runtime_soak_report_repository import (
        RuntimeSoakReportRepository,
    )

    session = get_session()
    try:
        repo = RuntimeSoakReportRepository(session)
        row = repo.get_latest_for_environment(environment=args.environment)
        if row is None:
            print_error(f"No soak report found for environment: {args.environment}")
            return 1
        print_header(f"Latest Soak Report ({args.environment})")
        print_json(row.report_json)
        return 0
    finally:
        session.close()


def handle_runbook_list(args: argparse.Namespace) -> int:
    if not _RUNBOOKS_DIR.exists():
        print_error(f"Runbooks directory not found: {_RUNBOOKS_DIR}")
        return 1
    files = sorted(_RUNBOOKS_DIR.rglob("*.md"))
    if not files:
        print("No runbooks found.")
        return 0
    print_header("Available Runbooks")
    for f in files:
        print(f"  {f.relative_to(_RUNBOOKS_DIR)}  ({f})")
    return 0


def handle_runbook_show(args: argparse.Namespace) -> int:
    if not _RUNBOOKS_DIR.exists():
        print_error(f"Runbooks directory not found: {_RUNBOOKS_DIR}")
        return 1
    direct = _RUNBOOKS_DIR / f"{args.name}.md"
    if direct.exists():
        target = direct
    else:
        matches = [f for f in _RUNBOOKS_DIR.rglob("*.md") if f.stem.lower() == args.name.lower()]
        if not matches:
            print_error(f"Runbook not found: {args.name}")
            return 1
        target = matches[0]
    print_header(f"Runbook: {target.stem}")
    print(f"Path: {target}")
    print()
    print(target.read_text(encoding="utf-8"))
    return 0


def handle_inspect_ingestion_readiness(args: argparse.Namespace) -> int:
    from uuid import uuid4

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
        }
    )
    return 0


def handle_verify_notification_events(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_notification_events as _bt_handle,
    )

    return _bt_handle(args)


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------


def _alert_to_dict(alert: OperationalAlert) -> dict:
    return {
        "alert_id": alert.alert_id,
        "fingerprint": alert.fingerprint,
        "alert_name": alert.alert_name,
        "category": alert.category,
        "severity": alert.severity,
        "status": alert.status,
        "source": alert.source,
        "environment": alert.environment,
        "job_name": alert.job_name,
        "strategy_id": alert.strategy_id,
        "summary": alert.summary,
        "recommended_action": alert.recommended_action,
        "created_at": alert.created_at,
        "updated_at": alert.updated_at,
        "acknowledged_at": alert.acknowledged_at,
        "acknowledged_by": alert.acknowledged_by,
        "resolved_at": alert.resolved_at,
        "resolved_by": alert.resolved_by,
        "snoozed_until": alert.snoozed_until,
        "notes": alert.notes,
    }
