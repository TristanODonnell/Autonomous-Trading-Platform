from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core import (
    runtime_control_state_repository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.broker_order_repository import (
    BrokerOrderRepository,
)

RuntimeControlStateRepository = runtime_control_state_repository.RuntimeControlStateRepository


@dataclass(frozen=True)
class KillSwitchResult:
    status: str
    kill_switch_active: bool
    canceled_order_count: int
    reason: str
    triggered_by: str
    triggered_at: datetime


@dataclass(frozen=True)
class ControlActionResult:
    status: str
    trading_paused: bool
    reason: str
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class TradingModeChangeResult:
    mode: str
    previous_mode: str
    reason: str
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class StrategyControlSnapshot:
    strategy_id: str
    enabled: bool
    status: str
    reason: str | None
    updated_by: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ControlsStateSnapshot:
    kill_switch_active: bool
    trading_enabled: bool
    trading_paused: bool
    trading_mode: str
    reason: str | None
    updated_by: str | None
    updated_at: datetime
    strategies: list[StrategyControlSnapshot]


class RuntimeControlStateWriter(Protocol):
    def get_or_create_global_state(self): ...

    def activate_kill_switch(
        self,
        *,
        triggered_by: str,
        reason: str,
        triggered_at: datetime,
    ) -> None: ...

    def release_kill_switch(
        self,
        *,
        reason: str | None,
        updated_by: str,
    ): ...

    def set_trading_paused(
        self,
        *,
        paused: bool,
        reason: str | None,
        updated_by: str,
    ): ...

    def set_trading_mode(
        self,
        *,
        trading_mode: str,
        reason: str | None,
        updated_by: str,
    ): ...


class BrokerOrderWriter(Protocol):
    def mark_open_orders_cancelled_by_kill_switch(
        self,
        *,
        cancelled_at: datetime,
        reason: str,
    ) -> int: ...


class AuditLogWriter(Protocol):
    def record_operator_action(
        self,
        *,
        action: str,
        actor: str,
        reason: str,
        occurred_at: datetime,
        metadata: dict,
    ) -> None: ...


class RuntimeControlService:
    _VALID_TRADING_MODES = {"simulation", "paper", "live"}
    _ACTIVE_PAPER_STATES = {"approved_for_paper_trading"}
    _ACTIVE_LIVE_STATES = {"approved_for_live_trading"}
    _ACTIVE_STRATEGY_STATES = _ACTIVE_PAPER_STATES | _ACTIVE_LIVE_STATES
    _ALLOWED_TRADING_MODE_TRANSITIONS = {
        ("simulation", "simulation"),
        ("simulation", "paper"),
        ("paper", "simulation"),
        ("paper", "paper"),
        ("paper", "live"),
        ("live", "paper"),
        ("live", "live"),
    }

    def __init__(
        self,
        session: Session,
        runtime_control_repo: RuntimeControlStateWriter | None = None,
        broker_order_repo: BrokerOrderWriter | None = None,
        audit_log_repo: AuditLogWriter | None = None,
    ) -> None:
        self.session = session
        self.runtime_control_repo = runtime_control_repo or RuntimeControlStateRepository(
            session=session
        )
        self.broker_order_repo = broker_order_repo or BrokerOrderRepository(session=session)
        self.audit_log_repo = audit_log_repo or AuditLogRepository(session=session)

    def activate_kill_switch(
        self,
        *,
        triggered_by: str,
        reason: str,
    ) -> KillSwitchResult:
        triggered_at = datetime.now(UTC)

        canceled_order_count = self.broker_order_repo.mark_open_orders_cancelled_by_kill_switch(
            cancelled_at=triggered_at,
            reason=reason,
        )

        self.runtime_control_repo.activate_kill_switch(
            triggered_by=triggered_by,
            reason=reason,
            triggered_at=triggered_at,
        )

        self.audit_log_repo.record_operator_action(
            action="KILL_SWITCH_ACTIVATED",
            actor=triggered_by,
            reason=reason,
            occurred_at=triggered_at,
            metadata={
                "canceled_order_count": canceled_order_count,
                "source": "api",
            },
        )

        self.session.commit()

        return KillSwitchResult(
            status="halted",
            kill_switch_active=True,
            canceled_order_count=canceled_order_count,
            reason=reason,
            triggered_by=triggered_by,
            triggered_at=triggered_at,
        )

    def pause_trading(
        self,
        *,
        updated_by: str,
        rationale: str,
    ) -> ControlActionResult:
        updated_at = datetime.now(UTC)
        state = self.runtime_control_repo.set_trading_paused(
            paused=True,
            reason=rationale,
            updated_by=updated_by,
        )

        self.audit_log_repo.record_operator_action(
            action="TRADING_PAUSED",
            actor=updated_by,
            reason=rationale,
            occurred_at=updated_at,
            metadata={
                "source": "api",
                "trading_paused": True,
            },
        )

        self.session.commit()

        return ControlActionResult(
            status="paused",
            trading_paused=state.trading_paused,
            reason=rationale,
            updated_by=updated_by,
            updated_at=state.updated_at,
        )

    def resume_trading(
        self,
        *,
        updated_by: str,
        rationale: str,
    ) -> ControlActionResult:
        updated_at = datetime.now(UTC)
        current = self.runtime_control_repo.get_or_create_global_state()

        if current.kill_switch_enabled:
            state = self.runtime_control_repo.release_kill_switch(
                reason=rationale,
                updated_by=updated_by,
            )
        else:
            state = self.runtime_control_repo.set_trading_paused(
                paused=False,
                reason=rationale,
                updated_by=updated_by,
            )

        self.audit_log_repo.record_operator_action(
            action="TRADING_RESUMED",
            actor=updated_by,
            reason=rationale,
            occurred_at=updated_at,
            metadata={
                "source": "api",
                "trading_paused": False,
            },
        )

        self.session.commit()

        return ControlActionResult(
            status="resumed",
            trading_paused=state.trading_paused,
            reason=rationale,
            updated_by=updated_by,
            updated_at=state.updated_at,
        )

    def update_trading_mode(
        self,
        *,
        updated_by: str,
        mode: str,
        rationale: str | None = None,
    ) -> TradingModeChangeResult:
        if mode not in self._VALID_TRADING_MODES:
            raise ValueError(f"Unsupported trading mode: {mode}")

        current_state = self.runtime_control_repo.get_or_create_global_state()
        previous_mode = current_state.trading_mode
        if (previous_mode, mode) not in self._ALLOWED_TRADING_MODE_TRANSITIONS:
            raise ValueError(f"Invalid trading mode transition: {previous_mode} -> {mode}")

        reason = rationale or f"Trading mode changed from {previous_mode} to {mode}"
        updated_at = datetime.now(UTC)
        state = self.runtime_control_repo.set_trading_mode(
            trading_mode=mode,
            reason=reason,
            updated_by=updated_by,
        )

        self.audit_log_repo.record_operator_action(
            action="TRADING_MODE_CHANGED",
            actor=updated_by,
            reason=reason,
            occurred_at=updated_at,
            metadata={
                "source": "api",
                "previous_mode": previous_mode,
                "mode": mode,
            },
        )

        self.session.commit()

        return TradingModeChangeResult(
            mode=state.trading_mode,
            previous_mode=previous_mode,
            reason=reason,
            updated_by=updated_by,
            updated_at=state.updated_at,
        )

    def get_controls_state(self) -> ControlsStateSnapshot:
        state = self.runtime_control_repo.get_or_create_global_state()
        return ControlsStateSnapshot(
            kill_switch_active=state.kill_switch_enabled,
            trading_enabled=state.trading_enabled,
            trading_paused=state.trading_paused,
            trading_mode=state.trading_mode,
            reason=state.reason,
            updated_by=state.updated_by,
            updated_at=state.updated_at,
            strategies=self._list_strategy_control_snapshots(),
        )

    def _list_strategy_control_snapshots(self) -> list[StrategyControlSnapshot]:
        governance_rows = self._list_active_strategy_governance_rows()
        strategy_ids = [row.strategy_id for row in governance_rows]
        control_states_by_strategy_id = self._get_strategy_control_states(strategy_ids)

        return [
            StrategyControlSnapshot(
                strategy_id=governance.strategy_id,
                enabled=self._resolve_strategy_enabled(
                    control_states_by_strategy_id.get(governance.strategy_id)
                ),
                status=self._resolve_strategy_status(governance.current_state),
                reason=(
                    control_states_by_strategy_id[governance.strategy_id].reason
                    if governance.strategy_id in control_states_by_strategy_id
                    else None
                ),
                updated_by=(
                    control_states_by_strategy_id[governance.strategy_id].updated_by
                    if governance.strategy_id in control_states_by_strategy_id
                    else None
                ),
                updated_at=(
                    control_states_by_strategy_id[governance.strategy_id].updated_at
                    if governance.strategy_id in control_states_by_strategy_id
                    else None
                ),
            )
            for governance in governance_rows
        ]

    def _list_active_strategy_governance_rows(self) -> list[StrategyGovernance]:
        stmt = (
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(self._ACTIVE_STRATEGY_STATES))
            .order_by(StrategyGovernance.strategy_id.asc(), StrategyGovernance.updated_at.desc())
        )
        rows_by_strategy_id: dict[str, StrategyGovernance] = {}
        for row in self.session.scalars(stmt).all():
            rows_by_strategy_id.setdefault(row.strategy_id, row)

        return list(rows_by_strategy_id.values())

    def _get_strategy_control_states(
        self,
        strategy_ids: list[str],
    ) -> dict[str, StrategyControlState]:
        if not strategy_ids:
            return {}

        stmt = select(StrategyControlState).where(
            StrategyControlState.strategy_id.in_(strategy_ids)
        )
        return {row.strategy_id: row for row in self.session.scalars(stmt).all()}

    def _resolve_strategy_status(self, governance_state: str) -> str:
        if governance_state in self._ACTIVE_LIVE_STATES:
            return "live"
        if governance_state in self._ACTIVE_PAPER_STATES:
            return "paper"
        return "off"

    def _resolve_strategy_enabled(self, control_state: StrategyControlState | None) -> bool:
        if control_state is None:
            return True

        return bool(control_state.enabled)
