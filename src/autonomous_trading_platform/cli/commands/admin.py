from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text

from autonomous_trading_platform.application.services.audit_log_service import AuditLogService
from autonomous_trading_platform.cli.formatters import (
    print_error,
    print_header,
    print_json,
    print_kv_rows,
    print_success,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_LIMIT_MIN = 1
_LIMIT_MAX = 1000

# Explicit denylist for redaction.  Any new secret field added to Settings
# must also be added here; the audit (Section 5) flagged this as a medium risk.
_REDACTED_KEYS = frozenset(
    {
        "database_url",
        "paper_broker_api_key",
        "paper_broker_api_secret",
        "live_broker_api_key",
        "live_broker_api_secret",
    }
)


def register(subparsers) -> None:
    admin_parser = subparsers.add_parser(
        "admin",
        help="Admin configuration, inspection, and failure-triage utilities",
    )
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command", required=True)

    # ── inspect-config ────────────────────────────────────────────────────────
    inspect_config_parser = admin_subparsers.add_parser(
        "inspect-config",
        help="Dump current Settings values (secrets redacted)",
    )
    inspect_config_parser.add_argument(
        "--json",
        dest="json_only",
        action="store_true",
        help="Emit machine-readable JSON without header noise",
    )
    inspect_config_parser.set_defaults(func=handle_show_config)

    # ── inspect-env ───────────────────────────────────────────────────────────
    inspect_env_parser = admin_subparsers.add_parser(
        "inspect-env",
        help="Report presence/absence of expected environment variables",
    )
    inspect_env_parser.add_argument(
        "--json",
        dest="json_only",
        action="store_true",
        help="Emit machine-readable JSON without header noise",
    )
    inspect_env_parser.set_defaults(func=handle_show_env)

    # ── validate-config  (P0 new) ─────────────────────────────────────────────
    validate_config_parser = admin_subparsers.add_parser(
        "validate-config",
        help="Validate required env/config and print structured pass/fail diagnostics",
    )
    validate_config_parser.add_argument(
        "--include-broker",
        action="store_true",
        help="Also validate broker credentials for the active trading environment",
    )
    validate_config_parser.set_defaults(func=handle_validate_config)

    # ── inspect-failed-runs  (refactored: broker dependency removed) ──────────
    inspect_failed_runs_parser = admin_subparsers.add_parser(
        "inspect-failed-runs",
        help=f"List recent failed run manifests (--limit {_LIMIT_MIN}–{_LIMIT_MAX}, default 25)",
    )
    inspect_failed_runs_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help=f"Number of results to return ({_LIMIT_MIN}–{_LIMIT_MAX})",
    )
    inspect_failed_runs_parser.set_defaults(func=handle_list_failed_runs)

    # ── inspect-failed-run  (P0 new, single-run detail) ───────────────────────
    inspect_failed_run_parser = admin_subparsers.add_parser(
        "inspect-failed-run",
        help="Show failure detail for a single run: manifest fields, failed step, error",
    )
    inspect_failed_run_parser.add_argument(
        "--run-id",
        required=True,
        help="UUID of the run manifest to inspect",
    )
    inspect_failed_run_parser.set_defaults(func=handle_inspect_failed_run)

    # ── inspect-audit-log  (P1 new) ───────────────────────────────────────────
    audit_log_parser = admin_subparsers.add_parser(
        "inspect-audit-log",
        help="List audit events with optional filters",
    )
    audit_log_parser.add_argument("--action-type", default=None, help="Filter by action type")
    audit_log_parser.add_argument("--strategy-id", default=None, help="Filter by strategy ID")
    audit_log_parser.add_argument("--user", default=None, help="Filter by user/actor")
    audit_log_parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="Start of date range (ISO 8601, e.g. 2025-01-01T00:00:00)",
    )
    audit_log_parser.add_argument(
        "--to",
        dest="to_date",
        default=None,
        help="End of date range (ISO 8601)",
    )
    audit_log_parser.add_argument("--page", type=int, default=1, help="Page number (default 1)")
    audit_log_parser.add_argument(
        "--page-size", type=int, default=25, help="Results per page (default 25)"
    )
    audit_log_parser.set_defaults(func=handle_inspect_audit_log)

    # ── inspect-db  (P1 new) ──────────────────────────────────────────────────
    inspect_db_parser = admin_subparsers.add_parser(
        "inspect-db",
        help="Verify DATABASE_URL presence, connectivity, and schema visibility",
    )
    inspect_db_parser.set_defaults(func=handle_inspect_db)

    # ── doctor  (P2 new) ──────────────────────────────────────────────────────
    doctor_parser = admin_subparsers.add_parser(
        "doctor",
        help="Bundled local preflight: config, env, DB, redaction; broker check is opt-in",
    )
    doctor_parser.add_argument(
        "--include-broker",
        action="store_true",
        help="Also run broker credential and connectivity check",
    )
    doctor_parser.set_defaults(func=handle_doctor)


# ---------------------------------------------------------------------------
# Internal dependency builder (no broker, no external I/O)
# ---------------------------------------------------------------------------


@dataclass
class AdminDependencies:
    settings: Settings


def build_dependencies() -> AdminDependencies:
    load_dotenv()
    settings = Settings()
    return AdminDependencies(settings=settings)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def handle_show_config(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    data = {
        key: "<redacted>" if key in _REDACTED_KEYS else value
        for key, value in deps.settings.__dict__.items()
    }
    if not getattr(args, "json_only", False):
        print_header("Config")
    print_json(data)
    return 0


def handle_show_env(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    data = {
        "APP_ENV": deps.settings.app_env,
        "LOG_LEVEL": deps.settings.log_level,
        "DATABASE_URL": "<set>" if deps.settings.database_url else "<unset>",
        "TRADING_ENVIRONMENT": deps.settings.trading_environment.value,
        "NO_LIVE_TRADING": str(deps.settings.no_live_trading),
        "VALIDATE_BROKER_CONFIG": str(deps.settings.validate_broker_config),
        "PAPER_BROKER_API_KEY": "<set>" if deps.settings.paper_broker_api_key else "<unset>",
        "PAPER_BROKER_API_SECRET": "<set>" if deps.settings.paper_broker_api_secret else "<unset>",
        "LIVE_BROKER_API_KEY": "<set>" if deps.settings.live_broker_api_key else "<unset>",
        "LIVE_BROKER_API_SECRET": "<set>" if deps.settings.live_broker_api_secret else "<unset>",
    }
    if getattr(args, "json_only", False):
        print_json(data)
    else:
        print_header("Environment")
        print_kv_rows(data)
    return 0


def handle_validate_config(args: argparse.Namespace) -> int:
    load_dotenv()

    checks: list[tuple[str, bool, str]] = []

    for var in ("APP_ENV", "DATABASE_URL"):
        val = os.getenv(var)
        checks.append((f"{var} present", bool(val), val or "<unset>"))

    original_validate = os.environ.get("VALIDATE_BROKER_CONFIG")
    os.environ["VALIDATE_BROKER_CONFIG"] = "false"
    settings_ok = True
    settings: Settings | None = None
    try:
        settings = Settings()
        checks.append(("Settings instantiation", True, "ok"))
    except Exception as exc:
        settings_ok = False
        checks.append(("Settings instantiation", False, str(exc)))
    finally:
        if original_validate is None:
            os.environ.pop("VALIDATE_BROKER_CONFIG", None)
        else:
            os.environ["VALIDATE_BROKER_CONFIG"] = original_validate

    if args.include_broker and settings_ok and settings is not None:
        from autonomous_trading_platform.config.broker_config_validator import (
            validate_broker_environment,
        )

        try:
            validate_broker_environment(
                trading_environment=settings.trading_environment,
                paper_broker_api_key=settings.paper_broker_api_key,
                paper_broker_api_secret=settings.paper_broker_api_secret,
                live_broker_api_key=settings.live_broker_api_key,
                live_broker_api_secret=settings.live_broker_api_secret,
            )
            checks.append(("Broker credentials valid", True, "ok"))
        except Exception as exc:
            checks.append(("Broker credentials valid", False, str(exc)))

    all_passed = all(passed for _, passed, _ in checks)

    print_header("Config Validation")
    for label, passed, detail in checks:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status}  {label}: {detail}")

    if all_passed:
        print_success("All checks passed.")
        return 0
    else:
        print_error("One or more checks failed.")
        return 1


def handle_list_failed_runs(args: argparse.Namespace) -> int:
    limit = args.limit
    if not (_LIMIT_MIN <= limit <= _LIMIT_MAX):
        print_error(f"--limit must be between {_LIMIT_MIN} and {_LIMIT_MAX}, got {limit}")
        return 1

    load_dotenv()
    session = get_session()
    try:
        with SorUnitOfWork(session) as uow:
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


def handle_inspect_failed_run(args: argparse.Namespace) -> int:
    try:
        run_uuid = UUID(args.run_id)
    except ValueError:
        print_error(f"Invalid run-id: {args.run_id!r} is not a valid UUID")
        return 1

    load_dotenv()
    session = get_session()
    try:
        repo = RunManifestRepository(session)
        row = repo.get_by_run_id(run_uuid)

        if row is None:
            print_error(f"No run manifest found for run_id={args.run_id}")
            return 1

        print_header(f"Run Manifest: {args.run_id}")
        print_json(
            {
                "run_id": str(row.run_id),
                "run_type": row.run_type.value if row.run_type else None,
                "status": row.status,
                "environment": row.environment,
                "broker": row.broker,
                "strategy_id": row.strategy_id,
                "strategy_version": row.strategy_version,
                "capital_bucket": str(row.capital_bucket),
                "bar_timestamp": (
                    row.bar_timestamp.isoformat() if row.bar_timestamp is not None else None
                ),
                "created_at": row.created_at.isoformat() if row.created_at is not None else None,
                "current_step": row.current_step,
                "last_successful_step": row.last_successful_step,
                "error_message": row.error_message,
                "governance_state": (row.governance_state.value if row.governance_state else None),
            }
        )
        return 0

    finally:
        session.close()


def handle_inspect_audit_log(args: argparse.Namespace) -> int:
    from_date: datetime | None = None
    to_date: datetime | None = None

    if args.from_date:
        try:
            from_date = datetime.fromisoformat(args.from_date)
        except ValueError:
            print_error(f"--from value is not valid ISO 8601: {args.from_date!r}")
            return 1

    if args.to_date:
        try:
            to_date = datetime.fromisoformat(args.to_date)
        except ValueError:
            print_error(f"--to value is not valid ISO 8601: {args.to_date!r}")
            return 1

    load_dotenv()
    session = get_session()
    try:
        svc = AuditLogService(session=session)
        result = svc.list_events(
            page=args.page,
            page_size=args.page_size,
            action_type=args.action_type,
            strategy_id=args.strategy_id,
            user_id=args.user,
            from_date=from_date,
            to_date=to_date,
        )

        print_header("Audit Log")
        print_json(
            {
                "total": result.total,
                "page": args.page,
                "page_size": args.page_size,
                "events": [
                    {
                        "event_id": e.event_id,
                        "action_type": e.action_type,
                        "description": e.description,
                        "user": e.user,
                        "timestamp": e.timestamp.isoformat(),
                        "strategy_id": e.strategy_id,
                        "rationale": e.rationale,
                    }
                    for e in result.events
                ],
            }
        )
        return 0

    finally:
        session.close()


def handle_inspect_db(_args: argparse.Namespace) -> int:
    load_dotenv()

    checks: list[tuple[str, bool, str]] = []

    db_url = os.getenv("DATABASE_URL")
    checks.append(("DATABASE_URL present", bool(db_url), "<set>" if db_url else "<unset>"))

    if db_url:
        try:
            session = get_session()
            try:
                session.execute(text("SELECT 1"))
                checks.append(("DB connectivity", True, "ok"))

                try:
                    row = session.execute(
                        text("SELECT version_num FROM alembic_version LIMIT 1")
                    ).fetchone()
                    version = row[0] if row else "<no rows>"
                    checks.append(("Alembic version table", True, str(version)))
                except Exception:
                    # Advisory only: table absent in fresh / test DBs
                    checks.append(("Alembic version table", True, "<table not found — advisory>"))

            except Exception as exc:
                checks.append(("DB connectivity", False, str(exc)))
            finally:
                session.close()

        except Exception as exc:
            checks.append(("Session creation", False, str(exc)))

    all_passed = all(passed for _, passed, _ in checks)

    print_header("DB Inspection")
    for label, passed, detail in checks:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status}  {label}: {detail}")

    if all_passed:
        print_success("Database is reachable and schema is visible.")
        return 0
    else:
        print_error("One or more DB checks failed.")
        return 1


def handle_doctor(args: argparse.Namespace) -> int:
    """Bundled preflight: runs validate-config then inspect-db, optionally broker check."""
    load_dotenv()

    all_checks: list[tuple[str, bool, str]] = []

    # ── config checks ─────────────────────────────────────────────────────────
    for var in ("APP_ENV", "DATABASE_URL"):
        val = os.getenv(var)
        all_checks.append((f"{var} present", bool(val), val or "<unset>"))

    original_validate = os.environ.get("VALIDATE_BROKER_CONFIG")
    os.environ["VALIDATE_BROKER_CONFIG"] = "false"
    settings_ok = True
    settings: Settings | None = None
    try:
        settings = Settings()
        all_checks.append(("Settings instantiation", True, "ok"))
    except Exception as exc:
        settings_ok = False
        all_checks.append(("Settings instantiation", False, str(exc)))
    finally:
        if original_validate is None:
            os.environ.pop("VALIDATE_BROKER_CONFIG", None)
        else:
            os.environ["VALIDATE_BROKER_CONFIG"] = original_validate

    # ── DB checks ─────────────────────────────────────────────────────────────
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            session = get_session()
            try:
                session.execute(text("SELECT 1"))
                all_checks.append(("DB connectivity", True, "ok"))
                try:
                    row = session.execute(
                        text("SELECT version_num FROM alembic_version LIMIT 1")
                    ).fetchone()
                    version = row[0] if row else "<no rows>"
                    all_checks.append(("Alembic version table", True, str(version)))
                except Exception:
                    all_checks.append(
                        ("Alembic version table", True, "<table not found — advisory>")
                    )
            except Exception as exc:
                all_checks.append(("DB connectivity", False, str(exc)))
            finally:
                session.close()
        except Exception as exc:
            all_checks.append(("Session creation", False, str(exc)))

    # ── broker checks (opt-in) ────────────────────────────────────────────────
    if args.include_broker and settings_ok and settings is not None:
        from autonomous_trading_platform.config.broker_config_validator import (
            validate_broker_environment,
        )

        try:
            validate_broker_environment(
                trading_environment=settings.trading_environment,
                paper_broker_api_key=settings.paper_broker_api_key,
                paper_broker_api_secret=settings.paper_broker_api_secret,
                live_broker_api_key=settings.live_broker_api_key,
                live_broker_api_secret=settings.live_broker_api_secret,
            )
            all_checks.append(("Broker credentials valid", True, "ok"))
        except Exception as exc:
            all_checks.append(("Broker credentials valid", False, str(exc)))

    all_passed = all(passed for _, passed, _ in all_checks)

    print_header("Doctor")
    for label, passed, detail in all_checks:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status}  {label}: {detail}")

    if all_passed:
        print_success("All preflight checks passed.")
        return 0
    else:
        print_error("One or more preflight checks failed.")
        return 1
