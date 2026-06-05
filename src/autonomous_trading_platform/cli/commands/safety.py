from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService as ApplicationRuntimeControlService,
)
from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderType, Side, TimeInForce
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.readers.order_activity_reader import (
    StubOrderActivityReader,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.safety.services.kill_switch_service import KillSwitchService
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.order_idempotency_service import (
    OrderIdempotencyService,
)
from autonomous_trading_platform.safety.services.order_throttle_service import (
    OrderThrottleService,
)
from autonomous_trading_platform.safety.services.pre_trade_risk_service import (
    PreTradeRiskService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import RuntimeGateService
from autonomous_trading_platform.storage.sor.models.kill_switch_state import (
    KILL_SWITCH_SINGLETON_ID,
    KillSwitchState,
)
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)

_SAFETY_AUDIT_COMPONENTS = {"safety", "controls", "safety.order_idempotency", "pre_trade_risk"}
_SAFETY_AUDIT_PREFIXES = (
    "KILL_SWITCH",
    "TRADING_",
    "ORDER_IDEMPOTENCY",
    "PORTFOLIO_SYMBOL_EXPOSURE",
    "SECTOR_CONCENTRATION",
)


@dataclass
class SafetyDependencies:
    session: Session
    settings: Settings
    runtime_gate_service: RuntimeGateService
    kill_switch_service: KillSwitchService
    live_gate_service: LiveTradingGateService
    app_runtime_control_service: ApplicationRuntimeControlService
    audit_log_repository: AuditLogRepository


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _parse_side(value: str) -> Side:
    try:
        return Side(value.lower())
    except ValueError as exc:
        choices = ", ".join(item.value for item in Side)
        raise argparse.ArgumentTypeError(f"--side must be one of: {choices}") from exc


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError("value must be decimal-compatible") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _require_text(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} is required")
    return stripped


def register(subparsers) -> None:
    safety_parser = subparsers.add_parser("safety", help="Safety operations")
    safety_subparsers = safety_parser.add_subparsers(dest="safety_command", required=True)

    status_parser = safety_subparsers.add_parser(
        "status",
        help="Show overall safety status",
    )
    status_parser.add_argument("--account-id")
    status_parser.set_defaults(func=handle_status)

    kill_status_parser = safety_subparsers.add_parser(
        "kill-switch-status",
        help="Read persisted kill-switch state",
    )
    kill_status_parser.set_defaults(func=handle_kill_switch_status)

    emergency_halt_parser = safety_subparsers.add_parser(
        "emergency-halt",
        help="Activate kill switch, cancel open orders, and audit the halt",
    )
    emergency_halt_parser.add_argument("--reason", required=True)
    emergency_halt_parser.add_argument("--triggered-by", required=True)
    emergency_halt_parser.set_defaults(func=handle_emergency_halt)

    release_parser = safety_subparsers.add_parser(
        "release-kill-switch",
        help="Release the kill switch with explicit confirmation",
    )
    release_parser.add_argument("--reason", required=True)
    release_parser.add_argument("--updated-by", required=True)
    release_parser.add_argument("--confirm-release", action="store_true", required=True)
    release_parser.set_defaults(func=handle_release_kill_switch)

    assert_parser = safety_subparsers.add_parser(
        "assert-gate",
        help="Return non-zero unless live trading gate is fully open",
    )
    assert_parser.add_argument("--account-id", required=True)
    assert_parser.set_defaults(func=handle_assert_gate)

    audit_parser = safety_subparsers.add_parser(
        "audit-log",
        help="Show recent safety-related audit log events",
    )
    audit_parser.add_argument("--limit", type=_positive_int, default=20)
    audit_parser.set_defaults(func=handle_audit_log)

    startup_parser = safety_subparsers.add_parser(
        "startup-check",
        help="Emit startup safety state; optionally persist startup audit evidence",
    )
    startup_parser.add_argument("--account-id", required=True)
    startup_parser.add_argument(
        "--emit-audit",
        action="store_true",
        help="Persist KILL_SWITCH_STATE_LOADED audit evidence",
    )
    startup_parser.set_defaults(func=handle_startup_check)

    pre_trade_parser = safety_subparsers.add_parser(
        "pre-trade-check",
        help="Validate local pre-trade safety gates without submitting an order",
    )
    pre_trade_parser.add_argument("--symbol", required=True)
    pre_trade_parser.add_argument("--side", required=True, type=_parse_side)
    pre_trade_parser.add_argument("--quantity", required=True, type=_parse_decimal)
    pre_trade_parser.add_argument("--price", required=True, type=_parse_decimal)
    pre_trade_parser.add_argument("--strategy-id", default="cli-pre-trade-check")
    pre_trade_parser.add_argument("--bar-timestamp")
    pre_trade_parser.set_defaults(func=handle_pre_trade_check)

    arm_parser = safety_subparsers.add_parser("arm-live", help="Arm live trading")
    arm_parser.add_argument("--reason", required=True)
    arm_parser.add_argument("--armed-by", required=True)
    arm_parser.add_argument("--expires-at")
    arm_parser.set_defaults(func=handle_arm_live)

    disarm_parser = safety_subparsers.add_parser("disarm-live", help="Disarm live trading")
    disarm_parser.set_defaults(func=handle_disarm_live)

    enable_parser = safety_subparsers.add_parser(
        "enable-kill-switch",
        help="Enable kill switch using the durable emergency-halt path",
    )
    enable_parser.add_argument("--reason", required=True)
    enable_parser.add_argument("--updated-by", required=True)
    enable_parser.set_defaults(func=handle_enable_kill_switch)

    disable_parser = safety_subparsers.add_parser(
        "disable-kill-switch",
        help="Disable kill switch; requires explicit confirmation",
    )
    disable_parser.add_argument("--reason", required=True)
    disable_parser.add_argument("--updated-by", required=True)
    disable_parser.add_argument("--confirm-release", action="store_true", required=True)
    disable_parser.set_defaults(func=handle_disable_kill_switch)

    gate_status_parser = safety_subparsers.add_parser(
        "gate-status",
        help="Get live trading gate status",
    )
    gate_status_parser.add_argument("--account-id", required=True)
    gate_status_parser.set_defaults(func=handle_gate_status)


def build_dependencies() -> SafetyDependencies:
    settings = Settings()
    environment_policy = EnvironmentSafetyPolicy(settings)
    session = get_session()

    runtime_gate_service = RuntimeGateService()
    runtime_control_repository = RuntimeControlStateRepository(session=session)
    kill_switch_repository = KillSwitchStateRepository(session=session)
    audit_log_repository = AuditLogRepository(session=session)
    kill_switch_service = KillSwitchService(
        repository=kill_switch_repository,
        runtime_control_repo=runtime_control_repository,
    )
    live_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
    )

    return SafetyDependencies(
        session=session,
        settings=settings,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
        live_gate_service=live_gate_service,
        app_runtime_control_service=ApplicationRuntimeControlService(session=session),
        audit_log_repository=audit_log_repository,
    )


def handle_status(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        account_id = args.account_id
        print_header("Safety Status")
        print_json(_build_status_payload(deps=deps, account_id=account_id))
        return 0
    finally:
        deps.session.close()


def handle_kill_switch_status(_args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        print_header("Kill Switch Status")
        print_json(_kill_switch_status_read_only(deps.session))
        return 0
    finally:
        deps.session.close()


def handle_gate_status(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        print_header("Gate Status")
        print_json(_gate_status_payload(deps=deps, account_id=args.account_id))
        return 0
    finally:
        deps.session.close()


def handle_assert_gate(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        status = _gate_status_payload(deps=deps, account_id=args.account_id)
        print_header("Assert Gate")
        print_json(status)
        return 0 if status["overall_ok"] else 1
    finally:
        deps.session.close()


def handle_arm_live(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        expires_at = parse_datetime(args.expires_at) if args.expires_at else None
        deps.runtime_gate_service.arm(
            reason=_require_text(args.reason, "--reason"),
            armed_by=_require_text(args.armed_by, "--armed-by"),
            expires_at=expires_at,
        )
        print_header("Arm Live")
        print_json(
            {
                **deps.runtime_gate_service.get_status(),
                "persistence": "process_local_only",
                "warning": (
                    "Runtime gate arming is not durable across separate CLI/runtime processes."
                ),
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_disarm_live(_args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        deps.runtime_gate_service.disarm()
        print_header("Disarm Live")
        print_json(
            {
                **deps.runtime_gate_service.get_status(),
                "persistence": "process_local_only",
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_emergency_halt(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        result = deps.app_runtime_control_service.activate_kill_switch(
            triggered_by=_require_text(args.triggered_by, "--triggered-by"),
            reason=_require_text(args.reason, "--reason"),
        )
        print_header("Emergency Halt")
        print_json(_kill_switch_result_to_dict(result))
        return 0
    finally:
        deps.session.close()


def handle_release_kill_switch(args: argparse.Namespace) -> int:
    if not args.confirm_release:
        raise ValueError("--confirm-release is required to release the kill switch")

    deps = build_dependencies()
    try:
        result = deps.app_runtime_control_service.resume_trading(
            updated_by=_require_text(args.updated_by, "--updated-by"),
            rationale=_require_text(args.reason, "--reason"),
        )
        print_header("Release Kill Switch")
        print_json(_control_action_result_to_dict(result))
        return 0
    finally:
        deps.session.close()


def handle_enable_kill_switch(args: argparse.Namespace) -> int:
    wrapper_args = argparse.Namespace(
        reason=args.reason,
        triggered_by=args.updated_by,
    )
    return handle_emergency_halt(wrapper_args)


def handle_disable_kill_switch(args: argparse.Namespace) -> int:
    wrapper_args = argparse.Namespace(
        reason=args.reason,
        updated_by=args.updated_by,
        confirm_release=args.confirm_release,
    )
    return handle_release_kill_switch(wrapper_args)


def handle_audit_log(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        rows, total = deps.audit_log_repository.list_events(
            page=1,
            page_size=max(args.limit * 5, args.limit),
        )
        filtered = [_audit_log_to_dict(row) for row in rows if _is_safety_audit_event(row)]
        print_header("Safety Audit Log")
        print_json(
            {
                "limit": args.limit,
                "source_total": total,
                "count": min(len(filtered), args.limit),
                "events": filtered[: args.limit],
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_startup_check(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        if args.emit_audit:
            deps.kill_switch_service.emit_startup_audit_event(audit_repo=deps.audit_log_repository)
            deps.session.commit()

        print_header("Startup Safety Check")
        print_json(
            {
                **_build_status_payload(deps=deps, account_id=args.account_id),
                "startup_audit_emitted": bool(args.emit_audit),
            }
        )
        return 0
    finally:
        deps.session.close()


def handle_pre_trade_check(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        now = datetime.now(UTC)
        bar_timestamp = parse_datetime(args.bar_timestamp) if args.bar_timestamp else now
        intent = _build_cli_order_intent(
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            price=args.price,
            strategy_id=args.strategy_id,
            now=now,
            bar_timestamp=bar_timestamp,
        )

        audit_repo = deps.audit_log_repository
        activity_reader = StubOrderActivityReader()
        PreTradeRiskService(
            settings=deps.settings,
            risk_state_reader=StubRiskStateReader(),
            audit_log_repo=audit_repo,
        ).assert_order_allowed(intent, now=now)
        OrderThrottleService(
            settings=deps.settings,
            order_activity_reader=activity_reader,
        ).assert_order_allowed_for_submission(
            order_intent=intent,
            now=now,
            bar_timestamp=bar_timestamp,
        )
        idempotency_key = OrderIdempotencyService(
            settings=deps.settings,
            order_activity_reader=activity_reader,
            audit_log_repository=audit_repo,
        ).assert_not_duplicate_within_window(intent, now=now)

        deps.session.rollback()
        print_header("Pre Trade Check")
        print_json(
            {
                "status": "allowed",
                "submitted_to_broker": False,
                "persisted": False,
                "assumptions": {
                    "current_exposure_reader": "stub_zero_exposure",
                    "order_activity_reader": "stub_no_prior_orders",
                    "submitted_to_broker": False,
                    "persisted": False,
                },
                "symbol": intent.symbol,
                "side": intent.side.value,
                "quantity": str(intent.qty),
                "price": str(intent.limit_price),
                "order_notional": str(args.quantity * args.price),
                "idempotency_key": idempotency_key,
            }
        )
        return 0
    except Exception as exc:
        deps.session.rollback()
        print_header("Pre Trade Check")
        print_json(
            {
                "status": "blocked",
                "reason": str(exc),
                "submitted_to_broker": False,
                "persisted": False,
            }
        )
        return 1
    finally:
        deps.session.close()


def _gate_status_payload(*, deps: SafetyDependencies, account_id: str) -> dict[str, Any]:
    build_and_config_ok = True
    account_ok = True
    runtime_ok = True
    kill_switch_ok = True
    errors: list[str] = []

    try:
        deps.live_gate_service.environment_policy.assert_environment_allowed()
    except Exception as exc:
        build_and_config_ok = False
        errors.append(str(exc))

    try:
        deps.live_gate_service.environment_policy.assert_account_allowed(account_id)
    except Exception as exc:
        account_ok = False
        errors.append(str(exc))

    try:
        deps.runtime_gate_service.assert_runtime_gate_open()
    except Exception as exc:
        runtime_ok = False
        errors.append(str(exc))

    kill_status = _kill_switch_status_read_only(deps.session)
    if kill_status["enabled"]:
        kill_switch_ok = False
        reason = kill_status.get("reason")
        errors.append(
            "Live trading blocked by external kill switch."
            + (f" Reason: {reason}" if reason else "")
        )

    return {
        "account_id": account_id,
        "build_and_config_ok": build_and_config_ok,
        "account_ok": account_ok,
        "runtime_ok": runtime_ok,
        "kill_switch_ok": kill_switch_ok,
        "overall_ok": build_and_config_ok and account_ok and runtime_ok and kill_switch_ok,
        "runtime_gate": deps.runtime_gate_service.get_status(),
        "kill_switch": kill_status,
        "errors": errors,
    }


def _build_status_payload(
    *,
    deps: SafetyDependencies,
    account_id: str | None,
) -> dict[str, Any]:
    controls = _runtime_control_status_read_only(deps.session)
    runtime_gate = deps.runtime_gate_service.get_status()
    kill_switch = _kill_switch_status_read_only(deps.session)
    gate = _gate_status_payload(deps=deps, account_id=account_id) if account_id else None
    environment = deps.settings.trading_environment.value
    environment_gate = {
        "trading_environment": environment,
        "no_live_trading": deps.settings.no_live_trading,
        "enable_live_trading": deps.settings.enable_live_trading,
        "include_live_modules": deps.settings.include_live_modules,
        "paper_allowed_account_ids": deps.settings.paper_allowed_account_ids,
        "live_allowed_account_ids": deps.settings.live_allowed_account_ids,
    }
    overall_ok = (
        not kill_switch["enabled"]
        and bool(controls["trading_enabled"])
        and not bool(controls["trading_paused"])
        and (gate["overall_ok"] if gate is not None else True)
    )

    return {
        "overall_ok": overall_ok,
        "environment_gate": environment_gate,
        "runtime_gate": runtime_gate,
        "kill_switch": kill_switch,
        "runtime_control": controls,
        "gate_status": gate,
        "notes": [
            "Runtime live gate is process-local in the current implementation.",
            "Use emergency-halt/release-kill-switch for durable protection changes.",
        ],
    }


def _kill_switch_status_read_only(session: Session) -> dict[str, Any]:
    state = session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)
    if state is None:
        return {
            "found": False,
            "enabled": False,
            "reason": None,
            "updated_by": None,
            "updated_at": None,
            "cleared_by": None,
            "cleared_at": None,
        }
    return {
        "found": True,
        "enabled": state.is_enabled,
        "reason": state.reason,
        "updated_by": state.updated_by,
        "updated_at": _iso(state.updated_at),
        "cleared_by": state.cleared_by,
        "cleared_at": _iso(state.cleared_at),
    }


def _runtime_control_status_read_only(session: Session) -> dict[str, Any]:
    state = session.get(RuntimeControlState, "global")
    if state is None:
        return {
            "found": False,
            "kill_switch_active": False,
            "trading_enabled": True,
            "trading_paused": False,
            "trading_mode": None,
            "reason": None,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "found": True,
        "kill_switch_active": state.kill_switch_enabled,
        "trading_enabled": state.trading_enabled,
        "trading_paused": state.trading_paused,
        "trading_mode": state.trading_mode,
        "reason": state.reason,
        "updated_by": state.updated_by,
        "updated_at": _iso(state.updated_at),
    }


def _kill_switch_result_to_dict(result) -> dict[str, Any]:
    return {
        "status": result.status,
        "kill_switch_active": result.kill_switch_active,
        "canceled_order_count": result.canceled_order_count,
        "reason": result.reason,
        "triggered_by": result.triggered_by,
        "triggered_at": _iso(result.triggered_at),
    }


def _control_action_result_to_dict(result) -> dict[str, Any]:
    return {
        "status": result.status,
        "trading_paused": result.trading_paused,
        "reason": result.reason,
        "updated_by": result.updated_by,
        "updated_at": _iso(result.updated_at),
    }


def _audit_log_to_dict(row) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "run_id": row.run_id,
        "event_type": row.event_type,
        "component": row.component,
        "event_timestamp": _iso(row.event_timestamp),
        "message": row.message,
        "metadata": row.event_metadata,
    }


def _is_safety_audit_event(row) -> bool:
    return row.component in _SAFETY_AUDIT_COMPONENTS or any(
        row.event_type.startswith(prefix) for prefix in _SAFETY_AUDIT_PREFIXES
    )


def _build_cli_order_intent(
    *,
    symbol: str,
    side: Side,
    quantity: Decimal,
    price: Decimal,
    strategy_id: str,
    now: datetime,
    bar_timestamp: datetime,
) -> OrderIntent:
    intent_id = uuid4()
    return OrderIntent(
        intent_id=intent_id,
        idempotency_key="cli-pre-trade-check",
        run_id=uuid4(),
        strategy_id=strategy_id,
        timestamp=now,
        bar_timestamp=bar_timestamp,
        symbol=symbol.upper(),
        side=side,
        qty=quantity,
        notional=None,
        order_type=OrderType.LIMIT,
        limit_price=price,
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        extended_hours=False,
        client_order_id=f"cli-pre-trade-{intent_id}",
        metadata={"source": "safety_cli_pre_trade_check"},
    )
