from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)

_ACTIVE_GOVERNANCE_STATES = {
    "approved_for_paper_trading",
    "approved_for_live_trading",
}


@dataclass(frozen=True)
class StrategyEnabledUpdateResult:
    strategy_id: str
    enabled: bool
    status: str
    reason: str
    updated_by: str
    updated_at: datetime


class StrategyControlService:
    def __init__(
        self,
        *,
        session: Session,
        control_state_repo: StrategyControlStateRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
    ) -> None:
        self._session = session
        self._control_state_repo = control_state_repo or StrategyControlStateRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)

    def set_enabled(
        self,
        *,
        strategy_id: str,
        enabled: bool,
        reason: str,
        updated_by: str,
    ) -> StrategyEnabledUpdateResult:
        governance = self._latest_governance(strategy_id)
        if governance is None:
            raise LookupError(f"Strategy not found: {strategy_id}")

        if enabled and governance.current_state not in _ACTIVE_GOVERNANCE_STATES:
            raise ValueError(
                "Strategy must be approved for paper or live trading before it can be enabled."
            )

        now = datetime.now(UTC)
        state = self._control_state_repo.set_enabled(
            strategy_id=strategy_id,
            enabled=enabled,
            reason=reason,
            updated_by=updated_by,
            updated_at=now,
        )

        action = "STRATEGY_ENABLED" if enabled else "STRATEGY_DISABLED"
        self._audit_log_repo.record_operator_action(
            action=action,
            actor=updated_by,
            reason=reason,
            occurred_at=now,
            component="strategies",
            metadata={
                "strategy_id": strategy_id,
                "enabled": enabled,
                "governance_state": governance.current_state,
            },
        )
        self._session.flush()
        self._session.commit()

        return StrategyEnabledUpdateResult(
            strategy_id=strategy_id,
            enabled=state.enabled,
            status=self._resolve_status(governance.current_state),
            reason=reason,
            updated_by=updated_by,
            updated_at=now,
        )

    def _latest_governance(self, strategy_id: str) -> StrategyGovernance | None:
        stmt = (
            select(StrategyGovernance)
            .where(StrategyGovernance.strategy_id == strategy_id)
            .order_by(StrategyGovernance.updated_at.desc())
            .limit(1)
        )
        return cast(StrategyGovernance | None, self._session.scalars(stmt).one_or_none())

    def _resolve_status(self, governance_state: str) -> str:
        if governance_state == "approved_for_live_trading":
            return "live"
        if governance_state == "approved_for_paper_trading":
            return "paper"
        return "off"
