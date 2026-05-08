from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    StrategyState,
    TimeInForce,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
    StrategyRuntimeState,
)
from autonomous_trading_platform.storage.sor.repositories.queries import (
    recent_activity_repository,
)

RecentActivityRepository = recent_activity_repository.RecentActivityRepository


def test_lists_recent_activity_from_cross_source_events(db_session: Session) -> None:
    now = datetime(2026, 5, 8, 16, 0, tzinfo=UTC)
    run_id = uuid4()
    intent_id = uuid4()

    db_session.add_all(
        [
            AuditLogRow(
                event_id="audit-1",
                run_id=None,
                event_type="pause_trading",
                component="controls",
                event_timestamp=now,
                message="Operator paused trading",
                event_metadata={"severity": "warning"},
            ),
            OrderIntents(
                intent_id=intent_id,
                idempotency_key="idem-activity",
                run_id=run_id,
                strategy_id="momentum_v1",
                timestamp=now - timedelta(minutes=1),
                bar_timestamp=now - timedelta(minutes=1),
                symbol="AAPL",
                side=Side.BUY,
                qty=Decimal("2"),
                notional=None,
                order_type=OrderType.MARKET,
                limit_price=None,
                stop_price=None,
                time_in_force=TimeInForce.DAY,
                extended_hours=False,
                client_order_id="client-activity",
                meta=None,
            ),
            Fill(
                fill_id="fill-activity",
                broker_order_id="broker-activity",
                intent_id=intent_id,
                run_id=run_id,
                timestamp=now - timedelta(minutes=1),
                symbol="AAPL",
                side=Side.BUY,
                quantity=Decimal("2"),
                price=Decimal("190.50"),
                fees=None,
                liquidity=None,
                venue=None,
                meta=None,
            ),
            RiskSnapshot(
                snapshot_id=uuid4(),
                run_id=run_id,
                timestamp=now - timedelta(minutes=2),
                gross_exposure=Decimal("100000"),
                net_exposure=Decimal("75000"),
                leverage=2.5,
                drawdown_pct=0.03,
                limits={},
                utilization={},
                is_blocked=True,
                block_reasons=None,
            ),
            StrategyRuntimeState(
                strategy_id="momentum_v1",
                current_state=StrategyState.IN_POSITION,
                last_signal_at=now - timedelta(minutes=3),
                last_transition_at=now - timedelta(minutes=3),
                cooldown_until=None,
                updated_at=now - timedelta(minutes=3),
                last_evaluated_bar_timestamp=None,
            ),
            BrokerOrder(
                broker_order_id="broker-activity",
                client_order_id="client-activity",
                intent_id=intent_id,
                run_id=run_id,
                broker="alpaca",
                account_id="paper",
                symbol="AAPL",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                extended_hours=False,
                qty=Decimal("2"),
                notional=None,
                limit_price=None,
                stop_price=None,
                status=OrderStatus.REJECTED,
                submitted_at=now - timedelta(minutes=4),
                updated_at=now - timedelta(minutes=4),
                filled_qty=Decimal("0"),
                avg_fill_price=None,
                last_error="broker rejected order",
                raw_broker_payload=None,
                requested_qty=Decimal("2"),
            ),
        ]
    )
    db_session.flush()

    repository = RecentActivityRepository(db_session)

    result = repository.list_recent_activity(limit=10)

    assert [item.event_type for item in result] == [
        "pause_trading",
        "trade_fill",
        "risk_alert",
        "strategy_state_change",
        "order_update",
    ]
    assert [item.timestamp for item in result] == sorted(
        [item.timestamp for item in result],
        reverse=True,
    )
    assert result[0].severity == "warning"
    assert result[1].description == "Filled buy 2 AAPL at 190.5 for momentum_v1"
    assert result[2].severity == "critical"
    assert result[4].severity == "error"


def test_applies_limit_after_merging_sources(db_session: Session) -> None:
    now = datetime(2026, 5, 8, 16, 0, tzinfo=UTC)
    for index in range(3):
        db_session.add(
            AuditLogRow(
                event_id=f"audit-{index}",
                run_id=None,
                event_type="operator_action",
                component="controls",
                event_timestamp=now - timedelta(minutes=index),
                message=f"Audit event {index}",
                event_metadata=None,
            )
        )
    db_session.flush()

    repository = RecentActivityRepository(db_session)

    result = repository.list_recent_activity(limit=2)

    assert [item.description for item in result] == [
        "Audit event 0",
        "Audit event 1",
    ]
