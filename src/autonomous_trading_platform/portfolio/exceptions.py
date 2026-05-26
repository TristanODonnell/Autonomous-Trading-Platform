# autonomous_trading_platform/portfolio/exceptions.py

from __future__ import annotations


class PortfolioError(Exception):
    """Base exception for all portfolio errors."""


class AllocationDeniedError(PortfolioError):
    """
    Raised when a strategy is denied capital allocation.
    Includes the strategy_id and the reason for denial.
    """

    def __init__(self, strategy_id: str, reason: str) -> None:
        self.strategy_id = strategy_id
        self.reason = reason
        super().__init__(f"Allocation denied for strategy '{strategy_id}': {reason}")


class NoPolicyFoundError(PortfolioError):
    """
    Raised when no active capital allocation policy exists for a
    given approval_status / performance_tier combination.
    """

    def __init__(self, approval_status: str, performance_tier: str | None) -> None:
        self.approval_status = approval_status
        self.performance_tier = performance_tier
        tier_str = f", tier={performance_tier}" if performance_tier else ""
        super().__init__(
            f"No active allocation policy found for status='{approval_status}'{tier_str}."
        )


class StrategyNotFoundError(PortfolioError):
    """
    Raised when a strategy_id cannot be found in the governance table.
    """

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        super().__init__(f"Strategy '{strategy_id}' not found in governance records.")


class InsufficientCapitalError(PortfolioError):
    """
    Raised when applying a policy would exceed total available capital.
    """

    def __init__(self, strategy_id: str, requested: float, available: float) -> None:
        self.strategy_id = strategy_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Strategy '{strategy_id}' requested ${requested:,.2f} "
            f"but only ${available:,.2f} is available."
        )


class AllocationBudgetExceededError(PortfolioError):
    """
    Raised when a proposed manual override would push aggregate portfolio
    allocation above the configured maximum (default 100%).
    """

    def __init__(
        self,
        strategy_id: str,
        requested_pct: float,
        resulting_total_pct: float,
        configured_max_pct: float,
    ) -> None:
        self.strategy_id = strategy_id
        self.requested_pct = requested_pct
        self.resulting_total_pct = resulting_total_pct
        self.configured_max_pct = configured_max_pct
        super().__init__(
            f"Allocation override for '{strategy_id}' ({requested_pct:.1%}) would bring "
            f"aggregate portfolio allocation to {resulting_total_pct:.1%}, "
            f"exceeding the configured maximum of {configured_max_pct:.1%}."
        )
