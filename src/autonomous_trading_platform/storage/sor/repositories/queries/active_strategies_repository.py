from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)

ACTIVE_PAPER_STATES = {"approved_for_paper_trading"}
ACTIVE_LIVE_STATES = {"approved_for_live_trading"}
ACTIVE_STATES = ACTIVE_PAPER_STATES | ACTIVE_LIVE_STATES


@dataclass(frozen=True)
class ActiveStrategyDashboardRow:
    strategy_id: str
    display_name: str
    strategy_type: str
    status: str
    todays_return: Decimal
    trade_count_today: int
    allocated_capital: Decimal
    enabled: bool


class ActiveStrategiesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active_strategies(
        self,
        *,
        now: datetime | None = None,
    ) -> list[ActiveStrategyDashboardRow]:
        if now is None:
            now = datetime.now(UTC)

        governance_rows = self._list_active_governance_rows()
        strategy_ids = [row.strategy_id for row in governance_rows]

        configs_by_strategy_id = self._get_configs_by_strategy_id(strategy_ids)
        trade_counts_by_strategy_id = self._get_trade_counts_today(strategy_ids, now=now)
        control_states_by_strategy_id = self._get_control_states_by_strategy_id(strategy_ids)
        overrides_by_strategy_id = self._get_active_overrides_by_strategy_id(
            strategy_ids,
            now=now,
        )
        policies_by_status_and_tier = self._get_active_policies_by_status_and_tier()

        rows: list[ActiveStrategyDashboardRow] = []
        for governance in governance_rows:
            config = configs_by_strategy_id.get(governance.strategy_id)
            override = overrides_by_strategy_id.get(governance.strategy_id)
            allocation_policy = self._resolve_policy(
                governance=governance,
                config=config,
                policies_by_status_and_tier=policies_by_status_and_tier,
            )
            status = self._resolve_status(governance.current_state)

            rows.append(
                ActiveStrategyDashboardRow(
                    strategy_id=str(governance.strategy_id),
                    display_name=self._resolve_display_name(governance, config),
                    strategy_type=self._resolve_strategy_type(config),
                    status=status,
                    todays_return=self._resolve_todays_return(config),
                    trade_count_today=trade_counts_by_strategy_id.get(
                        governance.strategy_id,
                        0,
                    ),
                    allocated_capital=self._resolve_allocated_capital(
                        override=override,
                        allocation_policy=allocation_policy,
                    ),
                    enabled=self._resolve_enabled(
                        status=status,
                        control_state=control_states_by_strategy_id.get(
                            governance.strategy_id,
                        ),
                    ),
                )
            )

        return rows

    def _list_active_governance_rows(self) -> list[StrategyGovernance]:
        stmt = (
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(ACTIVE_STATES))
            .order_by(StrategyGovernance.strategy_id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def _get_configs_by_strategy_id(
        self,
        strategy_ids: list[str],
    ) -> dict[str, StrategyConfigs]:
        if not strategy_ids:
            return {}

        stmt = select(StrategyConfigs).where(StrategyConfigs.strategy_id.in_(strategy_ids))
        return {row.strategy_id: row for row in self.session.scalars(stmt).all()}

    def _get_control_states_by_strategy_id(
        self,
        strategy_ids: list[str],
    ) -> dict[str, StrategyControlState]:
        if not strategy_ids:
            return {}

        stmt = select(StrategyControlState).where(
            StrategyControlState.strategy_id.in_(strategy_ids)
        )
        return {row.strategy_id: row for row in self.session.scalars(stmt).all()}

    def _get_trade_counts_today(
        self,
        strategy_ids: list[str],
        *,
        now: datetime,
    ) -> dict[str, int]:
        if not strategy_ids:
            return {}

        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        day_end = datetime.combine(now.date(), time.max, tzinfo=UTC)

        stmt = (
            select(OrderIntents.strategy_id, func.count(Fill.fill_id))
            .join(Fill, Fill.intent_id == OrderIntents.intent_id)
            .where(
                OrderIntents.strategy_id.in_(strategy_ids),
                Fill.timestamp >= day_start,
                Fill.timestamp <= day_end,
            )
            .group_by(OrderIntents.strategy_id)
        )

        return {
            str(strategy_id): int(count) for strategy_id, count in self.session.execute(stmt).all()
        }

    def _get_active_overrides_by_strategy_id(
        self,
        strategy_ids: list[str],
        *,
        now: datetime,
    ) -> dict[str, AllocationOverrides]:
        if not strategy_ids:
            return {}

        stmt = select(AllocationOverrides).where(
            AllocationOverrides.strategy_id.in_(strategy_ids),
            AllocationOverrides.is_active.is_(True),
        )
        rows = self.session.scalars(stmt).all()

        return {
            row.strategy_id: row for row in rows if row.expires_at is None or row.expires_at > now
        }

    def _get_active_policies_by_status_and_tier(
        self,
    ) -> dict[tuple[str, str | None], CapitalAllocationPolicies]:
        stmt = select(CapitalAllocationPolicies).where(
            CapitalAllocationPolicies.is_active.is_(True)
        )
        return {
            (row.approval_status, row.performance_tier): row
            for row in self.session.scalars(stmt).all()
        }

    def _resolve_policy(
        self,
        *,
        governance: StrategyGovernance,
        config: StrategyConfigs | None,
        policies_by_status_and_tier: dict[
            tuple[str, str | None],
            CapitalAllocationPolicies,
        ],
    ) -> CapitalAllocationPolicies | None:
        approval_status = self._resolve_policy_approval_status(governance.current_state)
        if approval_status is None:
            return None

        performance_tier = self._get_metadata_value(config, "performance_tier")
        if performance_tier is not None:
            policy = policies_by_status_and_tier.get((approval_status, str(performance_tier)))
            if policy is not None:
                return policy

        return policies_by_status_and_tier.get((approval_status, None))

    def _resolve_policy_approval_status(self, current_state: str) -> str | None:
        if current_state in ACTIVE_LIVE_STATES:
            return "approved_live"
        if current_state in ACTIVE_PAPER_STATES:
            return "approved_paper"
        return None

    def _resolve_status(self, current_state: str) -> str:
        if current_state in ACTIVE_LIVE_STATES:
            return "live"
        if current_state in ACTIVE_PAPER_STATES:
            return "paper"
        return "off"

    def _resolve_display_name(
        self,
        governance: StrategyGovernance,
        config: StrategyConfigs | None,
    ) -> str:
        display_name = self._get_metadata_value(config, "display_name")
        if display_name is None:
            display_name = self._get_config_value(config, "display_name")

        return str(display_name or governance.strategy_id)

    def _resolve_strategy_type(self, config: StrategyConfigs | None) -> str:
        if config is None:
            return "unknown"

        return str(
            config.strategy_type
            or self._get_metadata_value(config, "strategy_type")
            or self._get_config_value(config, "strategy_type")
            or "unknown"
        )

    def _resolve_todays_return(self, config: StrategyConfigs | None) -> Decimal:
        value = (
            self._get_metadata_value(config, "todays_return")
            or self._get_metadata_value(config, "today_return")
            or self._get_metadata_value(config, "daily_return")
        )

        if value is None:
            return Decimal("0.00")

        return Decimal(str(value))

    def _resolve_allocated_capital(
        self,
        *,
        override: AllocationOverrides | None,
        allocation_policy: CapitalAllocationPolicies | None,
    ) -> Decimal:
        if override is not None and override.max_position_size_usd is not None:
            return Decimal(str(override.max_position_size_usd))

        if allocation_policy is not None and allocation_policy.max_position_size_usd is not None:
            return Decimal(str(allocation_policy.max_position_size_usd))

        return Decimal("0.00")

    def _resolve_enabled(
        self,
        *,
        status: str,
        control_state: StrategyControlState | None,
    ) -> bool:
        if control_state is not None:
            return bool(control_state.enabled)

        return status != "off"

    def _get_metadata_value(
        self,
        config: StrategyConfigs | None,
        key: str,
    ) -> Any | None:
        if config is None or config.metadata_json is None:
            return None

        return config.metadata_json.get(key)

    def _get_config_value(
        self,
        config: StrategyConfigs | None,
        key: str,
    ) -> Any | None:
        if config is None:
            return None

        return config.config_json.get(key)
