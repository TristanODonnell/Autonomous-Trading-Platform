from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.exceptions import AllocationBudgetExceededError
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
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
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.cash_snapshot_repository import (
    CashSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)

logger = logging.getLogger(__name__)

_ACTIVE_ALLOCATION_STATES = {
    "approved_for_paper_trading": GovernanceState.APPROVED_PAPER,
    "approved_for_live_trading": GovernanceState.APPROVED_LIVE,
}


@dataclass(frozen=True)
class StrategyAllocationUpdateResult:
    strategy_id: str
    allocation_pct: Decimal
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
        operator_settings_repo: OperatorSettingsRepository | None = None,
        capital_policies_repo: CapitalAllocationPoliciesRepository | None = None,
        promotion_rules_repo: PromotionRulesRepository | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or Settings()
        self._allocation_overrides_repo = (
            allocation_overrides_repo or AllocationOverridesRepository(session)
        )
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._cash_snapshot_repo = cash_snapshot_repo or CashSnapshotRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._capital_policies_repo = capital_policies_repo or CapitalAllocationPoliciesRepository(
            session
        )
        self._promotion_rules_repo = promotion_rules_repo or PromotionRulesRepository(session)

    def override_allocation(
        self,
        *,
        strategy_id: str,
        allocation_pct: Decimal,
        reason: str,
        updated_by: str,
    ) -> StrategyAllocationUpdateResult:
        if allocation_pct < Decimal("0") or allocation_pct > Decimal("100"):
            raise ValueError("allocation_pct must be between 0 and 100.")

        if not self._strategy_exists(strategy_id):
            raise LookupError(f"Strategy not found: {strategy_id}")

        proposed_fraction = float(allocation_pct / Decimal("100"))
        resulting_total = self._compute_projected_aggregate_pct(strategy_id, proposed_fraction)
        max_total = self._get_max_total_allocation_pct()

        if resulting_total > max_total:
            now = datetime.now(UTC)
            self._audit_log_repo.record_operator_action(
                action="ALLOCATION_BUDGET_EXCEEDED",
                actor=updated_by,
                reason=reason,
                occurred_at=now,
                component="strategies",
                metadata={
                    "requested_strategy_id": strategy_id,
                    "requested_allocation_pct": str(allocation_pct),
                    "resulting_total_pct": f"{resulting_total:.4f}",
                    "configured_max_pct": f"{max_total:.4f}",
                    "actor": updated_by,
                },
            )
            self._session.flush()
            self._session.commit()
            raise AllocationBudgetExceededError(
                strategy_id=strategy_id,
                requested_pct=proposed_fraction,
                resulting_total_pct=resulting_total,
                configured_max_pct=max_total,
            )

        total_portfolio_capital = self._resolve_total_portfolio_capital()
        allocated_capital = allocation_pct / Decimal("100") * total_portfolio_capital

        now = datetime.now(UTC)

        self._allocation_overrides_repo.deactivate_override(strategy_id)
        self._session.flush()
        self._allocation_overrides_repo.create_override(
            AllocationOverrides(
                override_id=str(uuid4()),
                strategy_id=strategy_id,
                overridden_by=updated_by,
                override_reason=reason,
                max_pct_of_capital=float(allocation_pct / Decimal("100")),
                max_position_size_usd=None,
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
                "allocation_pct": str(allocation_pct),
                "allocated_capital": str(allocated_capital),
                "total_portfolio_capital": str(total_portfolio_capital),
                "resulting_aggregate_pct": f"{resulting_total:.4f}",
            },
        )
        self._session.flush()
        self._session.commit()

        return StrategyAllocationUpdateResult(
            strategy_id=strategy_id,
            allocation_pct=allocation_pct,
            allocated_capital=allocated_capital,
            total_portfolio_capital=total_portfolio_capital,
            reason=reason,
            updated_by=updated_by,
            updated_at=now,
        )

    def get_allocations_for_active_strategies(self) -> list[dict]:
        _ACTIVE_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}
        total_capital = self._resolve_total_portfolio_capital()

        governance_rows = list(
            self._session.scalars(
                select(StrategyGovernance).where(
                    StrategyGovernance.current_state.in_(_ACTIVE_STATES)
                )
            ).all()
        )

        seen: set[str] = set()
        results: list[dict] = []
        for gov in governance_rows:
            if gov.strategy_id in seen:
                continue
            seen.add(gov.strategy_id)

            config = self._session.get(StrategyConfigs, gov.strategy_id)
            display_name = (
                ((config.metadata_json or {}).get("display_name") or gov.strategy_id)
                if config
                else gov.strategy_id
            )

            override = self._allocation_overrides_repo.get_active_override(gov.strategy_id)
            allocation_pct = (
                Decimal(str(override.max_pct_of_capital)) * Decimal("100")
                if override and override.max_pct_of_capital is not None
                else None
            )
            allocated_capital = (
                allocation_pct / Decimal("100") * total_capital
                if allocation_pct is not None
                else None
            )
            results.append(
                {
                    "strategy_id": gov.strategy_id,
                    "display_name": str(display_name),
                    "allocation_pct": allocation_pct,
                    "allocated_capital": allocated_capital,
                    "total_portfolio_capital": total_capital,
                    "is_overridden": override is not None,
                    "overridden_by": override.overridden_by if override else None,
                    "reason": override.override_reason if override else None,
                    "updated_at": override.created_at if override else None,
                }
            )

        return results

    def get_aggregate_allocation_status(self) -> dict[str, float | bool]:
        max_total = self._get_max_total_allocation_pct()
        current_total = self._compute_current_aggregate_pct()
        remaining = max(max_total - current_total, 0.0)
        utilization = current_total / max_total if max_total > 0 else 0.0
        return {
            "aggregate_allocation_pct": current_total,
            "max_total_strategy_allocation_pct": max_total,
            "remaining_allocation_capacity_pct": remaining,
            "allocation_utilization": utilization,
            "allocation_budget_exceeded": current_total > max_total,
        }

    def _strategy_exists(self, strategy_id: str) -> bool:
        stmt = select(StrategyGovernance.strategy_id).where(
            StrategyGovernance.strategy_id == strategy_id
        )
        return self._session.execute(stmt).first() is not None

    def _compute_projected_aggregate_pct(
        self,
        proposed_strategy_id: str,
        proposed_fraction: float,
    ) -> float:
        """
        Returns the aggregate allocation fraction that would result from applying
        the proposed override.

        Only enabled, allocation-participating strategies count. In this service,
        those are approved paper and approved live strategies. Research, disabled,
        retired, rejected, and draft strategies do not consume manual override budget.
        """
        entries = self._allocation_participating_entries()
        total = self._portfolio_engine().get_aggregate_allocation_pct(
            entries,
            proposed_overrides={proposed_strategy_id: proposed_fraction},
        )
        logger.info(
            "aggregate allocation projected",
            extra={
                "requested_strategy_id": proposed_strategy_id,
                "requested_allocation_pct": proposed_fraction,
                "resulting_total_pct": total,
                "participating_strategy_count": len(entries),
            },
        )
        return total

    def _compute_current_aggregate_pct(self) -> float:
        entries = self._allocation_participating_entries()
        total = self._portfolio_engine().get_aggregate_allocation_pct(entries)
        logger.info(
            "aggregate allocation calculated",
            extra={
                "aggregate_allocation_pct": total,
                "participating_strategy_count": len(entries),
            },
        )
        return total

    def _portfolio_engine(self) -> PortfolioEngine:
        return PortfolioEngine(
            policies_repo=self._capital_policies_repo,
            overrides_repo=self._allocation_overrides_repo,
            promotion_rules_repo=self._promotion_rules_repo,
            total_capital=float(self._resolve_total_portfolio_capital()),
        )

    def _allocation_participating_entries(
        self,
    ) -> list[tuple[str, GovernanceState, str | None]]:
        rows = list(
            self._session.scalars(
                select(StrategyGovernance)
                .where(StrategyGovernance.current_state.in_(_ACTIVE_ALLOCATION_STATES))
                .order_by(
                    StrategyGovernance.strategy_id.asc(),
                    StrategyGovernance.updated_at.desc(),
                )
            ).all()
        )
        controls = {
            row.strategy_id: row
            for row in self._session.scalars(select(StrategyControlState)).all()
        }
        latest_by_strategy: dict[str, StrategyGovernance] = {}
        for row in rows:
            latest_by_strategy.setdefault(row.strategy_id, row)

        entries: list[tuple[str, GovernanceState, str | None]] = []
        for row in latest_by_strategy.values():
            control = controls.get(row.strategy_id)
            if control is not None and not bool(control.enabled):
                continue
            state = _ACTIVE_ALLOCATION_STATES.get(row.current_state)
            if state is None:
                continue
            entries.append((row.strategy_id, state, self._performance_tier(row.strategy_id)))
        return entries

    def _performance_tier(self, strategy_id: str) -> str | None:
        config = self._session.get(StrategyConfigs, strategy_id)
        if config is None or config.metadata_json is None:
            return None
        value = config.metadata_json.get("performance_tier")
        return str(value) if value is not None else None

    def _get_max_total_allocation_pct(self) -> float:
        settings = self._operator_settings_repo.get_current()
        if settings is not None and settings.max_total_strategy_allocation_pct is not None:
            return float(settings.max_total_strategy_allocation_pct)
        return 1.0

    def _resolve_total_portfolio_capital(self) -> Decimal:
        latest_cash = self._cash_snapshot_repo.get_latest()
        if latest_cash is not None and latest_cash.equity is not None:
            return Decimal(latest_cash.equity)

        return Decimal(str(self._settings.initial_capital))
