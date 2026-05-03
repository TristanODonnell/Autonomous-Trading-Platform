# autonomous_trading_platform/portfolio/allocation_provider.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomous_trading_platform.goverance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.models import AllocationResult


@runtime_checkable
class IAllocationProvider(Protocol):
    """
    Contract for resolving capital allocation for a strategy.

    Simulation path:  SimulationAllocationProvider (flat math, no DB)
    Live/paper path:  PortfolioEngine (DB policies + governance gate)
    """

    def get_allocation(
        self,
        strategy_id: str,
        approval_status: GovernanceState | None,
        performance_tier: str | None = None,
    ) -> AllocationResult: ...

    @property
    def total_capital(self) -> float: ...
