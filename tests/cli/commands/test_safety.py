from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import safety
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.kill_switch_state import (
    KILL_SWITCH_SINGLETON_ID,
    KillSwitchState,
)
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)


def _extract_json(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output[output.index("{") :]))


def _session_factory(db_session: Session):
    return lambda: db_session


def _patch_cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "false")
    monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")


def test_safety_commands_are_registered() -> None:
    parser = build_parser()

    command_args = [
        ["safety", "status"],
        ["safety", "kill-switch-status"],
        ["safety", "emergency-halt", "--reason", "halt", "--triggered-by", "operator"],
        [
            "safety",
            "release-kill-switch",
            "--reason",
            "clear",
            "--updated-by",
            "operator",
            "--confirm-release",
        ],
        ["safety", "assert-gate", "--account-id", "paper-account"],
        ["safety", "audit-log"],
        ["safety", "startup-check", "--account-id", "paper-account"],
        [
            "safety",
            "pre-trade-check",
            "--symbol",
            "AAPL",
            "--side",
            "buy",
            "--quantity",
            "1",
            "--price",
            "100",
        ],
        ["safety", "arm-live", "--reason", "window", "--armed-by", "operator"],
        ["safety", "disarm-live"],
        ["safety", "enable-kill-switch", "--reason", "halt", "--updated-by", "operator"],
        [
            "safety",
            "disable-kill-switch",
            "--reason",
            "clear",
            "--updated-by",
            "operator",
            "--confirm-release",
        ],
        ["safety", "gate-status", "--account-id", "paper-account"],
    ]

    for argv in command_args:
        args = parser.parse_args(argv)
        assert args.domain == "safety"
        assert hasattr(args, "func")


def test_status_and_kill_switch_status_do_not_create_singletons(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert safety.handle_status(argparse.Namespace(account_id=None)) == 0
    payload = _extract_json(capsys.readouterr().out)
    assert payload["kill_switch"]["found"] is False
    assert payload["runtime_control"]["found"] is False

    assert safety.handle_kill_switch_status(argparse.Namespace()) == 0
    payload = _extract_json(capsys.readouterr().out)
    assert payload["found"] is False

    assert db_session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID) is None
    assert db_session.get(RuntimeControlState, "global") is None


def test_emergency_halt_cancels_open_orders_persists_and_audits(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))
    now = datetime(2026, 5, 8, 16, 0, tzinfo=UTC)
    db_session.add(
        BrokerOrder(
            broker_order_id="open-order",
            client_order_id="client-open-order",
            intent_id=uuid4(),
            run_id=uuid4(),
            broker="alpaca",
            account_id="paper",
            symbol="AAPL",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            qty=Decimal("1"),
            notional=None,
            limit_price=None,
            stop_price=None,
            status=OrderStatus.SUBMITTED,
            submitted_at=now,
            updated_at=now,
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            last_error=None,
            raw_broker_payload=None,
            requested_qty=Decimal("1"),
        )
    )
    db_session.flush()

    args = argparse.Namespace(reason="operator emergency halt", triggered_by="operator")

    assert safety.handle_emergency_halt(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    control_state = db_session.get(RuntimeControlState, "global")
    kill_state = db_session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)
    broker_order = db_session.get(BrokerOrder, "open-order")
    audit_log = db_session.query(AuditLogRow).one()

    assert payload["status"] == "halted"
    assert payload["kill_switch_active"] is True
    assert payload["canceled_order_count"] == 1
    assert control_state is not None
    assert control_state.kill_switch_enabled is True
    assert control_state.trading_enabled is False
    assert kill_state is not None
    assert kill_state.is_enabled is True
    assert broker_order is not None
    assert broker_order.status == OrderStatus.CANCELED
    assert audit_log.event_type == "KILL_SWITCH_ACTIVATED"


def test_release_kill_switch_requires_confirmation() -> None:
    args = argparse.Namespace(
        reason="clear",
        updated_by="operator",
        confirm_release=False,
    )

    with pytest.raises(ValueError, match="--confirm-release"):
        safety.handle_release_kill_switch(args)


def test_release_kill_switch_clears_state(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))

    assert (
        safety.handle_emergency_halt(argparse.Namespace(reason="halt", triggered_by="operator"))
        == 0
    )
    capsys.readouterr()

    assert (
        safety.handle_release_kill_switch(
            argparse.Namespace(
                reason="clear",
                updated_by="operator",
                confirm_release=True,
            )
        )
        == 0
    )
    payload = _extract_json(capsys.readouterr().out)

    control_state = db_session.get(RuntimeControlState, "global")
    kill_state = db_session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)

    assert payload["status"] == "resumed"
    assert control_state is not None
    assert control_state.kill_switch_enabled is False
    assert control_state.trading_enabled is True
    assert kill_state is not None
    assert kill_state.is_enabled is False


def test_assert_gate_returns_nonzero_when_runtime_gate_is_closed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))

    exit_code = safety.handle_assert_gate(argparse.Namespace(account_id="paper-account"))
    payload = _extract_json(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["runtime_ok"] is False
    assert payload["overall_ok"] is False


def test_arm_live_accepts_expires_at(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))

    assert (
        safety.handle_arm_live(
            argparse.Namespace(
                reason="supervised window",
                armed_by="operator",
                expires_at="2099-01-01T00:00:00Z",
            )
        )
        == 0
    )
    payload = _extract_json(capsys.readouterr().out)

    assert payload["armed"] is True
    assert payload["expires_at"] == "2099-01-01T00:00:00+00:00"
    assert payload["persistence"] == "process_local_only"


def test_audit_log_lists_safety_events(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))

    assert (
        safety.handle_emergency_halt(argparse.Namespace(reason="halt", triggered_by="operator"))
        == 0
    )
    capsys.readouterr()

    assert safety.handle_audit_log(argparse.Namespace(limit=20)) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["count"] >= 1
    assert payload["events"][0]["event_type"] == "KILL_SWITCH_ACTIVATED"


def test_pre_trade_check_is_local_and_does_not_persist_audit(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))

    assert (
        safety.handle_pre_trade_check(
            argparse.Namespace(
                symbol="aapl",
                side=Side.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                strategy_id="strategy",
                bar_timestamp="2026-05-08T16:00:00Z",
            )
        )
        == 0
    )
    payload = _extract_json(capsys.readouterr().out)

    assert payload["status"] == "allowed"
    assert payload["submitted_to_broker"] is False
    assert payload["persisted"] is False
    assert payload["symbol"] == "AAPL"
    assert db_session.query(AuditLogRow).count() == 0


def test_startup_check_can_emit_audit(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _patch_cli_env(monkeypatch)
    monkeypatch.setattr(safety, "get_session", _session_factory(db_session))

    assert (
        safety.handle_startup_check(argparse.Namespace(account_id="paper-account", emit_audit=True))
        == 0
    )
    payload = _extract_json(capsys.readouterr().out)
    audit_log = db_session.query(AuditLogRow).one()

    assert payload["startup_audit_emitted"] is True
    assert audit_log.event_type == "KILL_SWITCH_STATE_LOADED"
