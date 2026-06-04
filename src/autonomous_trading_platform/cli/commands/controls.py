from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import select

from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.application.services.strategy_control_service import (
    StrategyControlService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_json
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)

_ACTIVE_STRATEGY_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}
_CONTROL_FIXTURE_KEYS = {"trading_enabled", "trading_paused", "trading_mode"}
_TRADING_MODES = {"simulation", "paper", "live"}
_AUDIT_EVENT_TYPES = {
    "TRADING_PAUSED",
    "TRADING_RESUMED",
    "TRADING_ENABLED",
    "TRADING_DISABLED",
    "TRADING_MODE_CHANGED",
    "STRATEGY_ENABLED",
    "STRATEGY_DISABLED",
    "STRATEGY_ALLOCATION_OVERRIDDEN",
    "STRATEGY_ALLOCATION_OVERRIDE_CLEARED",
    "ALLOCATION_BUDGET_EXCEEDED",
}


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "controls",
        help="Inspect and update local runtime operator controls.",
    )
    controls_subparsers = parser.add_subparsers(dest="controls_command", required=True)

    state_parser = controls_subparsers.add_parser("state", help="Show current controls.")
    state_parser.add_argument("--json", action="store_true")
    state_parser.set_defaults(func=handle_state)

    pause_parser = controls_subparsers.add_parser("pause", help="Soft-pause trading.")
    _add_actor_reason(pause_parser)
    pause_parser.set_defaults(func=handle_pause)

    resume_parser = controls_subparsers.add_parser("resume", help="Resume soft-paused trading.")
    _add_actor_reason(resume_parser)
    resume_parser.set_defaults(func=handle_resume)

    mode_parser = controls_subparsers.add_parser("mode", help="Inspect or set trading mode.")
    mode_subparsers = mode_parser.add_subparsers(dest="mode_command", required=True)
    mode_show_parser = mode_subparsers.add_parser("show", help="Show trading mode.")
    mode_show_parser.set_defaults(func=handle_mode_show)
    mode_set_parser = mode_subparsers.add_parser("set", help="Set trading mode.")
    mode_set_parser.add_argument("--mode", required=True, choices=sorted(_TRADING_MODES))
    mode_set_parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required when setting mode to live.",
    )
    _add_actor_reason(mode_set_parser)
    mode_set_parser.set_defaults(func=handle_mode_set)

    strategy_parser = controls_subparsers.add_parser(
        "strategy",
        help="Inspect or update strategy-level controls.",
    )
    strategy_subparsers = strategy_parser.add_subparsers(
        dest="strategy_command",
        required=True,
    )
    strategy_list_parser = strategy_subparsers.add_parser("list", help="List strategy controls.")
    strategy_list_parser.add_argument("--json", action="store_true")
    strategy_list_parser.set_defaults(func=handle_strategy_list)
    strategy_disable_parser = strategy_subparsers.add_parser(
        "disable",
        help="Disable one active strategy.",
    )
    strategy_disable_parser.add_argument("--strategy-id", required=True)
    _add_actor_reason(strategy_disable_parser)
    strategy_disable_parser.set_defaults(func=handle_strategy_disable)
    strategy_enable_parser = strategy_subparsers.add_parser(
        "enable",
        help="Enable one approved strategy.",
    )
    strategy_enable_parser.add_argument("--strategy-id", required=True)
    _add_actor_reason(strategy_enable_parser)
    strategy_enable_parser.set_defaults(func=handle_strategy_enable)

    allocation_parser = controls_subparsers.add_parser(
        "allocation",
        help="Inspect and update allocation overrides.",
    )
    allocation_subparsers = allocation_parser.add_subparsers(
        dest="allocation_command",
        required=True,
    )
    allocation_list_parser = allocation_subparsers.add_parser(
        "list",
        help="List current allocations.",
    )
    allocation_list_parser.add_argument("--json", action="store_true")
    allocation_list_parser.set_defaults(func=handle_allocation_list)
    allocation_status_parser = allocation_subparsers.add_parser(
        "status",
        help="Show aggregate allocation usage.",
    )
    allocation_status_parser.set_defaults(func=handle_allocation_status)
    allocation_preview_parser = allocation_subparsers.add_parser(
        "preview",
        help="Preview an allocation override.",
    )
    allocation_preview_parser.add_argument("--strategy-id", required=True)
    allocation_preview_parser.add_argument("--allocation-pct", required=True, type=Decimal)
    allocation_preview_parser.set_defaults(func=handle_allocation_preview)
    allocation_set_parser = allocation_subparsers.add_parser(
        "set",
        help="Set a manual allocation override.",
    )
    allocation_set_parser.add_argument("--strategy-id", required=True)
    allocation_set_parser.add_argument("--allocation-pct", required=True, type=Decimal)
    allocation_set_parser.add_argument("--dry-run", action="store_true")
    _add_actor_reason(allocation_set_parser)
    allocation_set_parser.set_defaults(func=handle_allocation_set)
    allocation_clear_parser = allocation_subparsers.add_parser(
        "clear",
        help="Clear active allocation overrides for one strategy.",
    )
    allocation_clear_parser.add_argument("--strategy-id", required=True)
    allocation_clear_parser.add_argument("--dry-run", action="store_true")
    _add_actor_reason(allocation_clear_parser)
    allocation_clear_parser.set_defaults(func=handle_allocation_clear)

    validate_parser = controls_subparsers.add_parser(
        "validate",
        help="Validate a controls fixture without writing it.",
    )
    validate_parser.add_argument("--config", required=True, type=Path)
    validate_parser.set_defaults(func=handle_validate)

    diff_parser = controls_subparsers.add_parser(
        "diff",
        help="Diff current controls against a fixture.",
    )
    diff_parser.add_argument("--config", required=True, type=Path)
    diff_parser.set_defaults(func=handle_diff)

    seed_parser = controls_subparsers.add_parser(
        "seed",
        help="Apply a controls-only fixture.",
    )
    seed_parser.add_argument("--config", required=True, type=Path)
    seed_parser.add_argument("--dry-run", action="store_true")
    seed_parser.add_argument("--confirm-live", action="store_true")
    _add_actor_reason(seed_parser)
    seed_parser.set_defaults(func=handle_seed)

    audit_parser = controls_subparsers.add_parser(
        "audit-log",
        help="Show recent controls audit entries.",
    )
    audit_parser.add_argument("--limit", type=_positive_int, default=20)
    audit_parser.set_defaults(func=handle_audit_log)

    export_parser = controls_subparsers.add_parser(
        "export",
        help="Write a controls snapshot artifact.",
    )
    export_parser.add_argument("--output", required=True, type=Path)
    export_parser.set_defaults(func=handle_export)

    show_parser = controls_subparsers.add_parser(
        "show",
        help="Show current controls grouped by section (kill-switch, toggles, allocations, pending).",
    )
    show_parser.add_argument("--json", action="store_true")
    show_parser.set_defaults(func=handle_show)

    verify_parser = controls_subparsers.add_parser(
        "verify-runtime-gates",
        help="Report whether current controls would gate runtime behavior.",
    )
    verify_parser.add_argument("--strategy-id")
    verify_parser.add_argument("--mode", choices=sorted(_TRADING_MODES))
    verify_parser.add_argument("--updated-by")
    verify_parser.add_argument("--reason")
    verify_parser.set_defaults(func=handle_verify_runtime_gates)


def handle_state(args: argparse.Namespace) -> int:
    print_json(_controls_snapshot())
    return 0


def handle_show(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import handle_read_controls

    return handle_read_controls(args)


def handle_pause(args: argparse.Namespace) -> int:
    if not _has_actor_reason(args):
        return 1

    session = get_session()
    try:
        result = RuntimeControlService(session=session).pause_trading(
            updated_by=args.updated_by,
            rationale=args.reason,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Pause failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def handle_resume(args: argparse.Namespace) -> int:
    if not _has_actor_reason(args):
        return 1

    session = get_session()
    try:
        state = RuntimeControlStateRepository(session).get_global_state()
        if state is not None and state.kill_switch_enabled:
            print_error(
                "Cannot resume from controls while kill switch is active. "
                "Use `atp safety disable-kill-switch`."
            )
            return 1
        result = RuntimeControlService(session=session).resume_trading(
            updated_by=args.updated_by,
            rationale=args.reason,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Resume failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def handle_mode_show(args: argparse.Namespace) -> int:
    controls = _controls_snapshot()
    print_json(
        {
            "trading_mode": controls["trading_mode"],
            "reason": controls["reason"],
            "updated_by": controls["updated_by"],
            "updated_at": controls["updated_at"],
            "found": controls["found"],
        }
    )
    return 0


def handle_mode_set(args: argparse.Namespace) -> int:
    if args.mode == "live" and not args.confirm_live:
        print_error("Setting live mode requires --confirm-live.")
        return 1
    if not _has_actor_reason(args):
        return 1

    session = get_session()
    try:
        result = RuntimeControlService(session=session).update_trading_mode(
            updated_by=args.updated_by,
            mode=args.mode,
            rationale=args.reason,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Trading mode update failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def handle_strategy_list(args: argparse.Namespace) -> int:
    print_json({"strategies": _strategy_controls_snapshot()})
    return 0


def handle_strategy_disable(args: argparse.Namespace) -> int:
    return _set_strategy_enabled(args, enabled=False)


def handle_strategy_enable(args: argparse.Namespace) -> int:
    return _set_strategy_enabled(args, enabled=True)


def handle_allocation_list(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = StrategyAllocationService(session=session).get_allocations_for_active_strategies()
        print_json({"strategies": rows})
        return 0
    except Exception as exc:
        print_error(f"Allocation list failed: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_status(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        status = StrategyAllocationService(session=session).get_aggregate_allocation_status()
        print_json(status)
        return 0
    except Exception as exc:
        print_error(f"Allocation status failed: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_preview(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        result = StrategyAllocationService(session=session).preview_allocation_override(
            strategy_id=args.strategy_id,
            allocation_pct=args.allocation_pct,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        print_error(f"Allocation preview failed: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_set(args: argparse.Namespace) -> int:
    if not _has_actor_reason(args):
        return 1
    if args.dry_run:
        return handle_allocation_preview(args)

    session = get_session()
    try:
        result = StrategyAllocationService(session=session).override_allocation(
            strategy_id=args.strategy_id,
            allocation_pct=args.allocation_pct,
            reason=args.reason,
            updated_by=args.updated_by,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Allocation override failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_clear(args: argparse.Namespace) -> int:
    if not _has_actor_reason(args):
        return 1

    session = get_session()
    try:
        active = _active_allocation_override(session, args.strategy_id)
        if args.dry_run:
            print_json(
                {
                    "dry_run": True,
                    "strategy_id": args.strategy_id,
                    "active_override_found": active is not None,
                    "active_override": _allocation_override_payload(active) if active else None,
                }
            )
            return 0
        result = StrategyAllocationService(session=session).clear_allocation_override(
            strategy_id=args.strategy_id,
            reason=args.reason,
            updated_by=args.updated_by,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Allocation override clear failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def handle_validate(args: argparse.Namespace) -> int:
    fixture = _read_controls_fixture(args.config)
    if fixture is None:
        return 1
    print_json({"valid": True, "fixture": fixture})
    return 0


def handle_diff(args: argparse.Namespace) -> int:
    fixture = _read_controls_fixture(args.config)
    if fixture is None:
        return 1
    print_json({"diff": _controls_diff(_controls_snapshot(), fixture)})
    return 0


def handle_seed(args: argparse.Namespace) -> int:
    fixture = _read_controls_fixture(args.config)
    if fixture is None:
        return 1
    if not _has_actor_reason(args):
        return 1
    if fixture["controls"].get("trading_mode") == "live" and not args.confirm_live:
        print_error("Seeding live mode requires --confirm-live.")
        return 1

    preview = {"dry_run": args.dry_run, "diff": _controls_diff(_controls_snapshot(), fixture)}
    if args.dry_run:
        print_json(preview)
        return 0

    session = get_session()
    try:
        runtime = RuntimeControlService(session=session)
        controls = fixture["controls"]
        if "trading_enabled" in controls:
            runtime.set_trading_enabled(
                enabled=bool(controls["trading_enabled"]),
                updated_by=args.updated_by,
                rationale=args.reason,
            )
        if "trading_paused" in controls:
            if bool(controls["trading_paused"]):
                runtime.pause_trading(updated_by=args.updated_by, rationale=args.reason)
            else:
                state = RuntimeControlStateRepository(session).get_global_state()
                if state is not None and state.kill_switch_enabled:
                    raise RuntimeError("Cannot resume while kill switch is active.")
                runtime.resume_trading(updated_by=args.updated_by, rationale=args.reason)
        if "trading_mode" in controls:
            runtime.update_trading_mode(
                updated_by=args.updated_by,
                mode=controls["trading_mode"],
                rationale=args.reason,
            )

        strategy_service = StrategyControlService(session=session)
        for item in fixture["strategies"]:
            strategy_service.set_enabled(
                strategy_id=item["strategy_id"],
                enabled=bool(item["enabled"]),
                reason=args.reason,
                updated_by=args.updated_by,
            )

        allocation_service = StrategyAllocationService(session=session)
        for item in fixture["allocations"]:
            allocation_service.override_allocation(
                strategy_id=item["strategy_id"],
                allocation_pct=Decimal(str(item["allocation_pct"])),
                reason=args.reason,
                updated_by=args.updated_by,
            )
        print_json({"applied": True, **preview})
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Controls seed failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def handle_audit_log(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        events: list[dict[str, Any]] = []
        for action in sorted(_AUDIT_EVENT_TYPES):
            rows, _total = AuditLogRepository(session).list_events(
                page=1,
                page_size=args.limit,
                action_type=action,
            )
            events.extend(_audit_event_payload(row) for row in rows)
        events.sort(key=lambda row: row["event_timestamp"], reverse=True)
        print_json({"events": events[: args.limit], "limit": args.limit})
        return 0
    finally:
        session.close()


def handle_export(args: argparse.Namespace) -> int:
    payload = {"exported_at": datetime.now(UTC).isoformat(), "controls": _controls_snapshot()}
    _write_payload(args.output, payload)
    print_json({"wrote": str(args.output)})
    return 0


def handle_verify_runtime_gates(args: argparse.Namespace) -> int:
    controls = _controls_snapshot()
    strategy = None
    if args.strategy_id:
        strategy = next(
            (row for row in controls["strategies"] if row["strategy_id"] == args.strategy_id),
            None,
        )
    checks = {
        "kill_switch_blocks_runtime": bool(controls["kill_switch_active"]),
        "pause_blocks_runtime": bool(controls["trading_paused"]),
        "trading_disabled_blocks_runtime": not bool(controls["trading_enabled"]),
        "mode_matches_expected": args.mode is None or controls["trading_mode"] == args.mode,
        "strategy_disabled_blocks_runtime": (
            None if strategy is None else not bool(strategy["enabled"])
        ),
    }
    print_json(
        {
            "external_broker": False,
            "strategy_id": args.strategy_id,
            "mode": controls["trading_mode"],
            "checks": checks,
        }
    )
    return 0


def _set_strategy_enabled(args: argparse.Namespace, *, enabled: bool) -> int:
    if not _has_actor_reason(args):
        return 1
    session = get_session()
    try:
        result = StrategyControlService(session=session).set_enabled(
            strategy_id=args.strategy_id,
            enabled=enabled,
            reason=args.reason,
            updated_by=args.updated_by,
        )
        print_json(_dataclass_payload(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Strategy control update failed; rolled back: {exc}")
        return 1
    finally:
        session.close()


def _controls_snapshot() -> dict[str, Any]:
    session = get_session()
    try:
        state = RuntimeControlStateRepository(session).get_global_state()
        if state is None:
            payload = {
                "found": False,
                "kill_switch_active": False,
                "trading_enabled": True,
                "trading_paused": False,
                "trading_mode": "paper",
                "reason": None,
                "updated_by": None,
                "updated_at": None,
                "strategies": _strategy_controls_snapshot(session=session),
            }
        else:
            payload = _runtime_state_payload(state)
            payload["found"] = True
            payload["strategies"] = _strategy_controls_snapshot(session=session)
        payload["allocation_overrides"] = _allocation_overrides_snapshot(session)
        return payload
    finally:
        session.close()


def _runtime_state_payload(state: RuntimeControlState) -> dict[str, Any]:
    return {
        "kill_switch_active": state.kill_switch_enabled,
        "trading_enabled": state.trading_enabled,
        "trading_paused": state.trading_paused,
        "trading_mode": state.trading_mode,
        "reason": state.reason,
        "updated_by": state.updated_by,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


def _strategy_controls_snapshot(session=None) -> list[dict[str, Any]]:
    owns_session = session is None
    if session is None:
        session = get_session()
    try:
        governance_rows = list(
            session.scalars(
                select(StrategyGovernance)
                .where(StrategyGovernance.current_state.in_(_ACTIVE_STRATEGY_STATES))
                .order_by(
                    StrategyGovernance.strategy_id.asc(), StrategyGovernance.updated_at.desc()
                )
            ).all()
        )
        latest_governance: dict[str, StrategyGovernance] = {}
        for row in governance_rows:
            latest_governance.setdefault(row.strategy_id, row)
        controls = {
            row.strategy_id: row for row in session.scalars(select(StrategyControlState)).all()
        }
        return [
            {
                "strategy_id": strategy_id,
                "enabled": bool(controls[strategy_id].enabled) if strategy_id in controls else True,
                "status": _strategy_status(row.current_state),
                "reason": controls[strategy_id].reason if strategy_id in controls else None,
                "updated_by": controls[strategy_id].updated_by if strategy_id in controls else None,
                "updated_at": controls[strategy_id].updated_at.isoformat()
                if strategy_id in controls
                else None,
            }
            for strategy_id, row in latest_governance.items()
        ]
    finally:
        if owns_session:
            session.close()


def _allocation_overrides_snapshot(session) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(AllocationOverrides)
            .where(AllocationOverrides.is_active.is_(True))
            .order_by(AllocationOverrides.strategy_id.asc())
        ).all()
    )
    return [_allocation_override_payload(row) for row in rows]


def _allocation_override_payload(row: AllocationOverrides) -> dict[str, Any]:
    return {
        "override_id": row.override_id,
        "strategy_id": row.strategy_id,
        "allocation_pct": (
            Decimal(str(row.max_pct_of_capital)) * Decimal("100")
            if row.max_pct_of_capital is not None
            else None
        ),
        "overridden_by": row.overridden_by,
        "reason": row.override_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _active_allocation_override(session, strategy_id: str) -> AllocationOverrides | None:
    return cast(
        AllocationOverrides | None,
        session.scalars(
            select(AllocationOverrides)
            .where(AllocationOverrides.strategy_id == strategy_id)
            .where(AllocationOverrides.is_active.is_(True))
            .order_by(AllocationOverrides.created_at.desc())
        ).first(),
    )


def _read_controls_fixture(config_path: Path) -> dict[str, Any] | None:
    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return None
    try:
        text = config_path.read_text(encoding="utf-8")
        raw = json.loads(text) if config_path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        print_error(f"Failed to parse controls config: {exc}")
        return None
    if not isinstance(raw, dict):
        print_error("Controls fixture must be an object.")
        return None

    controls = raw.get("controls", {})
    strategies = raw.get("strategies", raw.get("strategy_controls", []))
    allocations = raw.get("allocations", raw.get("allocation_overrides", []))
    if not isinstance(controls, dict):
        print_error("controls must be an object.")
        return None
    if not isinstance(strategies, list) or not isinstance(allocations, list):
        print_error("strategies and allocations must be lists.")
        return None

    unknown = sorted(set(controls) - _CONTROL_FIXTURE_KEYS)
    if unknown:
        print_error(
            f"Unknown controls keys: {unknown}. Valid controls keys: "
            f"{sorted(_CONTROL_FIXTURE_KEYS)}. Kill switch belongs to safety."
        )
        return None
    if "trading_mode" in controls and controls["trading_mode"] not in _TRADING_MODES:
        print_error(f"Invalid trading_mode: {controls['trading_mode']}")
        return None

    normalized_strategies = []
    for index, item in enumerate(strategies):
        if not isinstance(item, dict):
            print_error(f"strategies[{index}] must be an object.")
            return None
        # Accept explicit strategy_id format or type+parameters format (shared fixture)
        if "strategy_id" in item:
            strategy_id = str(item["strategy_id"])
        elif "type" in item:
            try:
                from autonomous_trading_platform.research.strategy_generation.generators.utils import (
                    make_config,
                )

                config = make_config(item["type"], item.get("parameters", {}))
                strategy_id = config.strategy_id
            except Exception as exc:
                print_error(f"strategies[{index}]: could not resolve strategy_id from type: {exc}")
                return None
        else:
            print_error(f"strategies[{index}] requires strategy_id or type.")
            return None
        if "enabled" not in item:
            print_error(f"strategies[{index}] requires enabled.")
            return None
        normalized_strategies.append({"strategy_id": strategy_id, "enabled": bool(item["enabled"])})
        # If allocation_pct is embedded in the strategy entry (shared fixture format),
        # treat it as an allocation override entry.
        if item.get("allocation_pct") is not None:
            pct = Decimal(str(item["allocation_pct"]))
            # Convert fractional (0.60) to percentage (60) if needed
            if pct <= Decimal("1") and pct > Decimal("0"):
                pct = pct * Decimal("100")
            if pct < Decimal("0") or pct > Decimal("100"):
                print_error(f"strategies[{index}].allocation_pct must be between 0 and 100.")
                return None
            allocations = list(allocations) + [{"strategy_id": strategy_id, "allocation_pct": pct}]

    normalized_allocations = []
    for index, item in enumerate(allocations):
        if not isinstance(item, dict) or "strategy_id" not in item or "allocation_pct" not in item:
            print_error(f"allocations[{index}] requires strategy_id and allocation_pct.")
            return None
        allocation_pct = Decimal(str(item["allocation_pct"]))
        if allocation_pct < Decimal("0") or allocation_pct > Decimal("100"):
            print_error(f"allocations[{index}].allocation_pct must be between 0 and 100.")
            return None
        normalized_allocations.append(
            {"strategy_id": str(item["strategy_id"]), "allocation_pct": allocation_pct}
        )

    return {
        "controls": controls,
        "strategies": normalized_strategies,
        "allocations": normalized_allocations,
    }


def _controls_diff(current: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "controls": [
            {
                "key": key,
                "current": current.get(key),
                "new": value,
                "changed": current.get(key) != value,
            }
            for key, value in fixture["controls"].items()
        ],
        "strategies": fixture["strategies"],
        "allocations": fixture["allocations"],
    }


def _strategy_status(governance_state: str) -> str:
    if governance_state == "approved_for_live_trading":
        return "live"
    if governance_state == "approved_for_paper_trading":
        return "paper"
    return "off"


def _audit_event_payload(row) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "event_timestamp": row.event_timestamp.isoformat(),
        "event_type": row.event_type,
        "component": row.component,
        "message": row.message,
        "metadata": row.metadata_,
    }


def _dataclass_payload(value: Any) -> dict[str, Any]:
    return dict(value.__dict__)


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(_jsonable(payload), sort_keys=True), encoding="utf-8")
        return
    path.write_text(json.dumps(_jsonable(payload), indent=2, default=str), encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _has_actor_reason(args: argparse.Namespace) -> bool:
    if args.updated_by and args.reason:
        return True
    print_error("Mutating controls commands require --updated-by and --reason.")
    return False


def _add_actor_reason(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--updated-by")
    parser.add_argument("--reason")


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value
