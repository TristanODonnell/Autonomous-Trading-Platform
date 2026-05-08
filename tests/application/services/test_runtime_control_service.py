from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

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
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)


def test_activate_kill_switch_halts_trading_cancels_open_orders_and_audits(
    db_session: Session,
) -> None:
    now = datetime(2026, 5, 8, 16, 0, tzinfo=UTC)
    intent_id = uuid4()
    run_id = uuid4()
    db_session.add(
        BrokerOrder(
            broker_order_id="open-order",
            client_order_id="client-open-order",
            intent_id=intent_id,
            run_id=run_id,
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

    service = RuntimeControlService(session=db_session)

    result = service.activate_kill_switch(
        triggered_by="operator-user",
        reason="emergency halt",
    )

    control_state = db_session.get(RuntimeControlState, "global")
    broker_order = db_session.get(BrokerOrder, "open-order")
    audit_log = db_session.query(AuditLogRow).one()

    assert result.status == "halted"
    assert result.kill_switch_active is True
    assert result.canceled_order_count == 1
    assert control_state is not None
    assert control_state.kill_switch_enabled is True
    assert control_state.trading_enabled is False
    assert control_state.updated_by == "operator-user"
    assert broker_order is not None
    assert broker_order.status == OrderStatus.CANCELED
    assert broker_order.last_error == "emergency halt"
    assert audit_log.event_type == "KILL_SWITCH_ACTIVATED"
    assert audit_log.component == "controls"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["actor"] == "operator-user"
    assert audit_log.event_metadata["canceled_order_count"] == 1


def test_pause_and_resume_trading_update_state_and_audit(db_session: Session) -> None:
    service = RuntimeControlService(session=db_session)

    paused = service.pause_trading(
        updated_by="operator-user",
        rationale="market data incident",
    )
    resumed = service.resume_trading(
        updated_by="operator-user",
        rationale="market data recovered",
    )

    control_state = db_session.get(RuntimeControlState, "global")
    audit_logs = db_session.query(AuditLogRow).order_by(AuditLogRow.event_timestamp.asc()).all()

    assert paused.status == "paused"
    assert paused.trading_paused is True
    assert resumed.status == "resumed"
    assert resumed.trading_paused is False
    assert control_state is not None
    assert control_state.trading_paused is False
    assert control_state.reason == "market data recovered"
    assert [row.event_type for row in audit_logs] == [
        "TRADING_PAUSED",
        "TRADING_RESUMED",
    ]


def test_update_trading_mode_validates_transition_and_audits(
    db_session: Session,
) -> None:
    service = RuntimeControlService(session=db_session)

    result = service.update_trading_mode(
        updated_by="operator-user",
        mode="simulation",
        rationale="research run",
    )

    control_state = db_session.get(RuntimeControlState, "global")
    audit_log = db_session.query(AuditLogRow).one()

    assert result.previous_mode == "paper"
    assert result.mode == "simulation"
    assert control_state is not None
    assert control_state.trading_mode == "simulation"
    assert audit_log.event_type == "TRADING_MODE_CHANGED"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["previous_mode"] == "paper"
    assert audit_log.event_metadata["mode"] == "simulation"

    try:
        service.update_trading_mode(
            updated_by="operator-user",
            mode="live",
            rationale="unsafe jump",
        )
    except ValueError as exc:
        assert "Invalid trading mode transition" in str(exc)
    else:
        raise AssertionError("Expected invalid transition to raise ValueError")
