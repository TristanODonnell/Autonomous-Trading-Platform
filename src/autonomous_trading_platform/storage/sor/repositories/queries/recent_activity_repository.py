from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
    StrategyRuntimeState,
)

ActivitySeverity = Literal["info", "warning", "error", "critical"]

_VALID_SEVERITIES: set[str] = {"info", "warning", "error", "critical"}


@dataclass(frozen=True)
class RecentActivityItem:
    event_type: str
    description: str
    timestamp: datetime
    severity: ActivitySeverity


class RecentActivityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_recent_activity(self, *, limit: int = 20) -> list[RecentActivityItem]:
        safe_limit = min(max(limit, 1), 100)

        items = [
            *self._list_audit_events(safe_limit),
            *self._list_trade_fills(safe_limit),
            *self._list_risk_alerts(safe_limit),
            *self._list_strategy_state_changes(safe_limit),
            *self._list_broker_order_events(safe_limit),
        ]

        items.sort(key=lambda item: item.timestamp, reverse=True)
        return items[:safe_limit]

    def _list_audit_events(self, limit: int) -> list[RecentActivityItem]:
        stmt = select(AuditLogRow).order_by(desc(AuditLogRow.event_timestamp)).limit(limit)

        return [self._audit_to_item(row) for row in self.session.scalars(stmt).all()]

    def _list_trade_fills(self, limit: int) -> list[RecentActivityItem]:
        stmt = (
            select(Fill, OrderIntents.strategy_id)
            .join(OrderIntents, OrderIntents.intent_id == Fill.intent_id, isouter=True)
            .order_by(desc(Fill.timestamp))
            .limit(limit)
        )

        return [
            self._fill_to_item(fill, strategy_id)
            for fill, strategy_id in self.session.execute(stmt).all()
        ]

    def _list_risk_alerts(self, limit: int) -> list[RecentActivityItem]:
        stmt = (
            select(RiskSnapshot)
            .where(RiskSnapshot.is_blocked.is_(True))
            .order_by(desc(RiskSnapshot.timestamp))
            .limit(limit)
        )

        return [self._risk_snapshot_to_item(row) for row in self.session.scalars(stmt).all()]

    def _list_strategy_state_changes(self, limit: int) -> list[RecentActivityItem]:
        stmt = (
            select(StrategyRuntimeState)
            .order_by(desc(StrategyRuntimeState.last_transition_at))
            .limit(limit)
        )

        return [self._strategy_state_to_item(row) for row in self.session.scalars(stmt).all()]

    def _list_broker_order_events(self, limit: int) -> list[RecentActivityItem]:
        stmt = select(BrokerOrder).order_by(desc(BrokerOrder.updated_at)).limit(limit)

        return [self._broker_order_to_item(row) for row in self.session.scalars(stmt).all()]

    def _audit_to_item(self, row: AuditLogRow) -> RecentActivityItem:
        return RecentActivityItem(
            event_type=row.event_type,
            description=row.message,
            timestamp=self._normalize_timestamp(row.event_timestamp),
            severity=self._audit_severity(row),
        )

    def _fill_to_item(
        self,
        row: Fill,
        strategy_id: str | None,
    ) -> RecentActivityItem:
        strategy_suffix = f" for {strategy_id}" if strategy_id else ""
        return RecentActivityItem(
            event_type="trade_fill",
            description=(
                f"Filled {row.side.value} {self._format_decimal(row.quantity)} "
                f"{row.symbol} at {self._format_decimal(row.price)}{strategy_suffix}"
            ),
            timestamp=self._normalize_timestamp(row.timestamp),
            severity="info",
        )

    def _risk_snapshot_to_item(self, row: RiskSnapshot) -> RecentActivityItem:
        reasons = ", ".join(row.block_reasons or []) or "risk limits breached"
        return RecentActivityItem(
            event_type="risk_alert",
            description=f"Risk alert blocked trading: {reasons}",
            timestamp=self._normalize_timestamp(row.timestamp),
            severity="critical",
        )

    def _strategy_state_to_item(self, row: StrategyRuntimeState) -> RecentActivityItem:
        return RecentActivityItem(
            event_type="strategy_state_change",
            description=(f"Strategy {row.strategy_id} transitioned to {row.current_state.value}"),
            timestamp=self._normalize_timestamp(row.last_transition_at),
            severity="info",
        )

    def _broker_order_to_item(self, row: BrokerOrder) -> RecentActivityItem:
        return RecentActivityItem(
            event_type="order_update",
            description=(
                f"Broker order {row.broker_order_id} for {row.symbol} is {row.status.value}"
            ),
            timestamp=self._normalize_timestamp(row.updated_at),
            severity=self._order_severity(row),
        )

    def _audit_severity(self, row: AuditLogRow) -> ActivitySeverity:
        metadata_severity = None
        if row.metadata_ is not None:
            metadata_severity = row.metadata_.get("severity")

        if isinstance(metadata_severity, str) and metadata_severity in _VALID_SEVERITIES:
            return cast(ActivitySeverity, metadata_severity)

        event_type = row.event_type.lower()
        if "kill" in event_type or "emergency" in event_type:
            return "critical"
        if "error" in event_type or "failed" in event_type:
            return "error"
        if "pause" in event_type or "resume" in event_type or "mode" in event_type:
            return "warning"
        return "info"

    def _order_severity(self, row: BrokerOrder) -> ActivitySeverity:
        if row.status == OrderStatus.REJECTED or row.last_error:
            return "error"
        if row.status == OrderStatus.CANCELED:
            return "warning"
        return "info"

    def _format_decimal(self, value: Decimal) -> str:
        return format(value.normalize(), "f")

    def _normalize_timestamp(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.astimezone(UTC)
        return value.astimezone(UTC)
