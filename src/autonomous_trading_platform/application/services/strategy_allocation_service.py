from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.cash_snapshot_repository import (
    CashSnapshotRepository,
)


@dataclass(frozen=True)
class StrategyAllocationUpdateResult:
    strategy_id: str
    allocated_capital: Decimal
    total_portfolio_capital: Decimal
    reason: str
    updated_by: str
    updated_at: datetime


class StrategyAllocationService:
    def __init__(
        self,
        *,
        session: Session,
        settings: Settings | None = None,
        allocation_overrides_repo: AllocationOverridesRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        cash_snapshot_repo: CashSnapshotRepository | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or Settings()
        self._allocation_overrides_repo = (
            allocation_overrides_repo or AllocationOverridesRepository(session)
        )
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._cash_snapshot_repo = cash_snapshot_repo or CashSnapshotRepository(session)

    def override_allocation(
        self,
        *,
        strategy_id: str,
        allocated_capital: Decimal,
        reason: str,
        updated_by: str,
    ) -> StrategyAllocationUpdateResult:
        if allocated_capital < Decimal("0"):
            raise ValueError("allocated_capital must be non-negative.")

        if not self._strategy_exists(strategy_id):
            raise LookupError(f"Strategy not found: {strategy_id}")

        total_portfolio_capital = self._resolve_total_portfolio_capital()
        if allocated_capital > total_portfolio_capital:
            raise ValueError(
                "allocated_capital cannot exceed total portfolio capital "
                f"({total_portfolio_capital})."
            )

        now = datetime.now(UTC)

        self._allocation_overrides_repo.deactivate_override(strategy_id)
        self._session.flush()
        self._allocation_overrides_repo.create_override(
            AllocationOverrides(
                override_id=str(uuid4()),
                strategy_id=strategy_id,
                overridden_by=updated_by,
                override_reason=reason,
                max_pct_of_capital=None,
                max_position_size_usd=float(allocated_capital),
                max_drawdown_allowed=None,
                is_active=True,
                created_at=now,
                expires_at=None,
            )
        )

        self._audit_log_repo.record_operator_action(
            action="STRATEGY_ALLOCATION_OVERRIDDEN",
            actor=updated_by,
            reason=reason,
            occurred_at=now,
            component="strategies",
            metadata={
                "strategy_id": strategy_id,
                "allocated_capital": str(allocated_capital),
                "total_portfolio_capital": str(total_portfolio_capital),
            },
        )
        self._session.flush()

        return StrategyAllocationUpdateResult(
            strategy_id=strategy_id,
            allocated_capital=allocated_capital,
            total_portfolio_capital=total_portfolio_capital,
            reason=reason,
            updated_by=updated_by,
            updated_at=now,
        )

    def _strategy_exists(self, strategy_id: str) -> bool:
        stmt = select(StrategyGovernance.strategy_id).where(
            StrategyGovernance.strategy_id == strategy_id
        )
        return self._session.execute(stmt).first() is not None

    def _resolve_total_portfolio_capital(self) -> Decimal:
        latest_cash = self._cash_snapshot_repo.get_latest()
        if latest_cash is not None and latest_cash.equity is not None:
            return Decimal(latest_cash.equity)

        return Decimal(str(self._settings.initial_capital))
