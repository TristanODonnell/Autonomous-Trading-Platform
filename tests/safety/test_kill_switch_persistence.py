"""
Persistence tests for KillSwitchService (FINDING-03).

Verifies that kill-switch state survives simulated restarts (a new service
instance sees the previously persisted state) and that every path that
enables/disables the kill switch writes durable records to the SOR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.safety.errors import KillSwitchEnabledError
from autonomous_trading_platform.safety.services.kill_switch_service import KillSwitchService
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.kill_switch_state import KillSwitchState
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


def _make_service(
    db_session: Session,
    *,
    with_runtime_sync: bool = True,
) -> KillSwitchService:
    ks_repo = KillSwitchStateRepository(session=db_session)
    runtime_repo = RuntimeControlStateRepository(session=db_session) if with_runtime_sync else None
    return KillSwitchService(repository=ks_repo, runtime_control_repo=runtime_repo)


# ── default disabled state ────────────────────────────────────────────────────


def test_default_state_is_disabled_when_no_row_exists(db_session: Session) -> None:
    service = _make_service(db_session)

    assert service.is_enabled() is False

    row = db_session.get(KillSwitchState, "current")
    assert row is not None
    assert row.is_enabled is False
    assert row.reason is None


# ── enable persists to DB ─────────────────────────────────────────────────────


def test_enable_persists_to_db(db_session: Session) -> None:
    service = _make_service(db_session)
    service.enable(reason="market circuit breaker", updated_by="ops-user")

    row = db_session.get(KillSwitchState, "current")
    assert row is not None
    assert row.is_enabled is True
    assert row.reason == "market circuit breaker"
    assert row.updated_by == "ops-user"
    assert row.updated_at is not None
    assert row.cleared_by is None
    assert row.cleared_at is None


# ── disable persists cleared fields ──────────────────────────────────────────


def test_disable_persists_cleared_fields(db_session: Session) -> None:
    service = _make_service(db_session)
    service.enable(reason="market circuit breaker", updated_by="ops-user")
    service.disable(reason="market stabilised", updated_by="ops-user")

    row = db_session.get(KillSwitchState, "current")
    assert row is not None
    assert row.is_enabled is False
    assert row.reason == "market stabilised"
    assert row.cleared_by == "ops-user"
    assert row.cleared_at is not None


# ── assert_not_enabled blocks on persisted enabled state ─────────────────────


def test_assert_not_enabled_blocks_when_persisted_state_is_enabled(db_session: Session) -> None:
    service = _make_service(db_session)
    service.enable(reason="critical failure", updated_by="ops-user")

    with pytest.raises(KillSwitchEnabledError, match="critical failure"):
        service.assert_not_enabled()


# ── simulated restart: new instance sees persisted state ─────────────────────


def test_new_instance_reads_persisted_enabled_state(db_session: Session) -> None:
    """Simulate a process restart: a new KillSwitchService sees what was persisted."""
    first = _make_service(db_session)
    first.enable(reason="scheduled halt", updated_by="scheduler")

    # "restart": new service object, same DB session (same data).
    second = _make_service(db_session)
    assert second.is_enabled() is True

    with pytest.raises(KillSwitchEnabledError):
        second.assert_not_enabled()


def test_new_instance_reads_disabled_after_persist_then_clear(db_session: Session) -> None:
    first = _make_service(db_session)
    first.enable(reason="halt", updated_by="ops")
    first.disable(reason="cleared", updated_by="ops")

    second = _make_service(db_session)
    assert second.is_enabled() is False
    second.assert_not_enabled()


# ── startup audit event ───────────────────────────────────────────────────────


def test_startup_emits_kill_switch_state_loaded_event(db_session: Session) -> None:
    service = _make_service(db_session)
    service.enable(reason="pre-existing halt", updated_by="previous-operator")

    audit_repo = AuditLogRepository(session=db_session)
    service.emit_startup_audit_event(audit_repo=audit_repo)

    events = (
        db_session.query(AuditLogRow)
        .filter(AuditLogRow.event_type == "KILL_SWITCH_STATE_LOADED")
        .all()
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.component == "safety"
    assert ev.event_metadata is not None
    assert ev.event_metadata["is_enabled"] is True
    assert ev.event_metadata["kill_switch_reason"] == "pre-existing halt"
    assert ev.event_metadata["updated_by"] == "previous-operator"


def test_startup_event_reflects_disabled_state(db_session: Session) -> None:
    service = _make_service(db_session)
    audit_repo = AuditLogRepository(session=db_session)
    service.emit_startup_audit_event(audit_repo=audit_repo)

    events = (
        db_session.query(AuditLogRow)
        .filter(AuditLogRow.event_type == "KILL_SWITCH_STATE_LOADED")
        .all()
    )
    assert len(events) == 1
    assert events[0].event_metadata["is_enabled"] is False


# ── RuntimeControlState sync ─────────────────────────────────────────────────


def test_enable_syncs_runtime_control_state(db_session: Session) -> None:
    service = _make_service(db_session, with_runtime_sync=True)
    service.enable(reason="sync test", updated_by="ops")

    control_state = db_session.get(RuntimeControlState, "global")
    assert control_state is not None
    assert control_state.kill_switch_enabled is True


def test_disable_syncs_runtime_control_state(db_session: Session) -> None:
    service = _make_service(db_session, with_runtime_sync=True)
    service.enable(reason="sync test", updated_by="ops")
    service.disable(reason="cleared", updated_by="ops")

    control_state = db_session.get(RuntimeControlState, "global")
    assert control_state is not None
    assert control_state.kill_switch_enabled is False


# ── API path (RuntimeControlService) also writes KillSwitchState ─────────────


def test_api_activate_kill_switch_writes_kill_switch_state(db_session: Session) -> None:
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)
    intent_id = uuid4()
    run_id = uuid4()
    db_session.add(
        BrokerOrder(
            broker_order_id="open-order-ks",
            client_order_id="client-open-order-ks",
            intent_id=intent_id,
            run_id=run_id,
            broker="alpaca",
            account_id="paper",
            symbol="MSFT",
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

    svc = RuntimeControlService(session=db_session)
    svc.activate_kill_switch(triggered_by="operator", reason="emergency")

    ks_row = db_session.get(KillSwitchState, "current")
    assert ks_row is not None
    assert ks_row.is_enabled is True
    assert ks_row.reason == "emergency"
    assert ks_row.updated_by == "operator"


def test_api_resume_clears_kill_switch_state(db_session: Session) -> None:
    svc = RuntimeControlService(session=db_session)
    # Enable via runtime control service (also writes KillSwitchState)
    svc.kill_switch_repo.enable(
        reason="halt",
        updated_by="ops",
        updated_at=datetime.now(UTC),
    )
    # Manually set RuntimeControlState so resume_trading sees kill_switch_enabled=True
    ctrl = svc.runtime_control_repo.get_or_create_global_state()
    ctrl.kill_switch_enabled = True
    db_session.flush()

    svc.resume_trading(updated_by="ops", rationale="all clear")

    ks_row = db_session.get(KillSwitchState, "current")
    assert ks_row is not None
    assert ks_row.is_enabled is False
    assert ks_row.cleared_by == "ops"


# ── scheduler / trading-cycle enforcement ────────────────────────────────────


def test_trading_cycle_block_reason_reflects_persisted_kill_switch(db_session: Session) -> None:
    from autonomous_trading_platform.runtime.services.runtime_control_service import (
        RuntimeControlService as CycleRCS,
    )

    ks_service = _make_service(db_session, with_runtime_sync=True)
    ks_service.enable(reason="halt all", updated_by="ops")

    runtime_control_repo = RuntimeControlStateRepository(session=db_session)
    cycle_service = CycleRCS(repository=runtime_control_repo)

    block_reason = cycle_service.get_cycle_block_reason(expected_trading_mode="paper")
    assert block_reason == "kill_switch_enabled"


def test_trading_cycle_not_blocked_when_kill_switch_disabled(db_session: Session) -> None:
    from autonomous_trading_platform.runtime.services.runtime_control_service import (
        RuntimeControlService as CycleRCS,
    )

    runtime_control_repo = RuntimeControlStateRepository(session=db_session)
    cycle_service = CycleRCS(repository=runtime_control_repo)

    # Default state: no kill switch, trading enabled, paper mode
    block_reason = cycle_service.get_cycle_block_reason(expected_trading_mode="paper")
    assert block_reason is None
