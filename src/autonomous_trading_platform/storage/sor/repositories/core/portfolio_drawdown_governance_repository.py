# storage/sor/repositories/core/portfolio_drawdown_governance_repository.py

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from autonomous_trading_platform.storage.sor.models.portfolio_drawdown_governance_state import (
    GOVERNANCE_SINGLETON_ID,
    PortfolioDrawdownGovernanceState,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class PortfolioDrawdownGovernanceRepository(BaseRepository):
    """
    CRUD for the portfolio drawdown governance singleton row.

    Always operates on the single row with id="current".  The repository
    auto-creates that row with safe defaults (nothing paused, no peak recorded)
    if it does not yet exist — callers never need to handle None.
    """

    def get_current_state(self) -> PortfolioDrawdownGovernanceState:
        row = cast(
            PortfolioDrawdownGovernanceState | None,
            self.session.get(PortfolioDrawdownGovernanceState, GOVERNANCE_SINGLETON_ID),
        )
        if row is None:
            row = PortfolioDrawdownGovernanceState(
                governance_id=GOVERNANCE_SINGLETON_ID,
                peak_equity_usd=None,
                governance_paused=False,
                rebalancing_paused=False,
                breach_detected_at=None,
                breach_drawdown_pct=None,
                breach_threshold_pct=None,
                triggered_action=None,
                kill_switch_auto_activated=False,
                operator_acknowledged_at=None,
                operator_acknowledged_by=None,
                recovery_mode=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.session.add(row)
            self.session.flush()
        return row

    def update(self, **kwargs) -> PortfolioDrawdownGovernanceState:
        """Apply field updates to the singleton row and flush."""
        state = self.get_current_state()
        for field, value in kwargs.items():
            setattr(state, field, value)
        state.updated_at = datetime.now(UTC)
        self.session.flush()
        return state
