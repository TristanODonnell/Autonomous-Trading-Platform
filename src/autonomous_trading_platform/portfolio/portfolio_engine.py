# autonomous_trading_platform/portfolio/portfolio_engine.py

from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.exceptions import (
    AllocationDeniedError,
    InsufficientCapitalError,
    NoPolicyFoundError,
)
from autonomous_trading_platform.portfolio.models import (
    AllocationResult,
    CriterionResult,
    PromotionEligibility,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)

# States that are permitted to receive capital allocation.
# PROPOSED, REJECTED, RETIRED are all blocked.
_ALLOCATABLE_STATES: frozenset[GovernanceState] = frozenset(
    {
        GovernanceState.APPROVED_RESEARCH,
        GovernanceState.APPROVED_PAPER,
        GovernanceState.APPROVED_LIVE,
    }
)


class PortfolioEngine:
    def __init__(
        self,
        policies_repo: CapitalAllocationPoliciesRepository,
        overrides_repo: AllocationOverridesRepository,
        promotion_rules_repo: PromotionRulesRepository,
        total_capital: float,
    ) -> None:
        self._policies = policies_repo
        self._overrides = overrides_repo
        self._promotion_rules = promotion_rules_repo
        self._total_capital = total_capital

    def get_allocation(
        self,
        strategy_id: str,
        approval_status: GovernanceState | None,
        performance_tier: str | None = None,
    ) -> AllocationResult:
        """
        Resolve the capital allocation for a strategy.

        Steps:
        1. Assert the strategy is in an allocatable state (TASK-185 gate).
        2. Load the active policy for (approval_status, performance_tier).
        3. Check for an active manual override for this strategy.
        4. Merge policy + override (override fields win where set).
        5. Compute allocated_capital_usd from max_pct_of_capital * total_capital.
        6. Return AllocationResult.

        Raises:
            AllocationDeniedError: if the strategy is not in an allocatable state.
            NoPolicyFoundError: if no active policy exists for the given status/tier.
            InsufficientCapitalError: if the computed allocation exceeds available capital.
        """
        # Step 1 — gate: only approved states may allocate (TASK-185)
        self._assert_allocatable(strategy_id, approval_status)
        assert approval_status is not None  # narrowed: _assert_allocatable raises if None

        # Step 2 — load base policy
        policy = self._policies.get_active_policy(
            approval_status=approval_status.value,
            performance_tier=performance_tier,
        )
        if policy is None:
            raise NoPolicyFoundError(
                approval_status=approval_status.value,
                performance_tier=performance_tier,
            )

        # Step 3 — load any active manual override
        now = datetime.now(tz=UTC)
        override = self._overrides.get_active_override(strategy_id=strategy_id, now=now)

        # Step 4 — merge: override fields take precedence where they are not None
        max_pct = (
            override.max_pct_of_capital
            if override is not None and override.max_pct_of_capital is not None
            else policy.max_pct_of_capital
        )
        max_position_size_usd = (
            override.max_position_size_usd
            if override is not None and override.max_position_size_usd is not None
            else policy.max_position_size_usd
        )
        max_drawdown = (
            override.max_drawdown_allowed
            if override is not None and override.max_drawdown_allowed is not None
            else policy.max_drawdown_allowed
        )

        # Step 5 — compute dollar allocation
        allocated_usd = max_pct * self._total_capital
        if allocated_usd > self._total_capital:
            raise InsufficientCapitalError(
                strategy_id=strategy_id,
                requested=allocated_usd,
                available=self._total_capital,
            )

        # Step 6 — return result
        return AllocationResult(
            strategy_id=strategy_id,
            approval_status=approval_status,
            max_pct_of_capital=max_pct,
            max_position_size_usd=max_position_size_usd,
            max_drawdown_allowed=max_drawdown,
            allocated_capital_usd=allocated_usd,
            override_applied=override is not None,
            override_id=override.override_id if override is not None else None,
            policy_id=policy.policy_id,
        )

    def get_allocations_for_many(
        self,
        strategies: list[tuple[str, GovernanceState, str | None]],
    ) -> dict[str, AllocationResult]:

        results: dict[str, AllocationResult] = {}
        for strategy_id, approval_status, performance_tier in strategies:
            try:
                results[strategy_id] = self.get_allocation(
                    strategy_id=strategy_id,
                    approval_status=approval_status,
                    performance_tier=performance_tier,
                )
            except (AllocationDeniedError, NoPolicyFoundError):
                continue
        return results

    def assert_strategy_approved_for_allocation(
        self,
        strategy_id: str,
        approval_status: GovernanceState | None,
    ) -> None:
        """
        Public guard method for use by the execution layer.
        Raises AllocationDeniedError if the strategy cannot allocate capital.
        """
        self._assert_allocatable(strategy_id, approval_status)

    def _assert_allocatable(
        self,
        strategy_id: str,
        approval_status: GovernanceState | None,
    ) -> None:
        if approval_status is None or approval_status not in _ALLOCATABLE_STATES:
            state_label = approval_status.value if approval_status is not None else "None"
            raise AllocationDeniedError(
                strategy_id=strategy_id,
                reason=(
                    f"Strategy is in state '{state_label}' which is not "
                    f"approved for capital allocation. "
                    f"Must be one of: {[s.value for s in _ALLOCATABLE_STATES]}."
                ),
            )

    def check_promotion_eligibility(
        self,
        strategy_id: str,
        from_state: GovernanceState,
        to_state: GovernanceState,
        metrics: dict[str, float],
    ) -> PromotionEligibility:

        rule = self._promotion_rules.get_rules_for_transition(
            from_status=from_state.value,
            to_status=to_state.value,
        )

        if rule is None:
            return PromotionEligibility(
                strategy_id=strategy_id,
                from_state=from_state,
                to_state=to_state,
                is_eligible=True,
                criteria_results={},
            )

        criteria: dict[str, CriterionResult] = {}

        # min_sharpe
        if rule.min_sharpe is not None:
            actual = metrics.get("sharpe")
            passed = actual is not None and actual >= rule.min_sharpe
            criteria["min_sharpe"] = CriterionResult(
                passed=passed,
                required=rule.min_sharpe,
                actual=actual,
                detail=(
                    f"Sharpe {actual:.2f} >= {rule.min_sharpe:.2f}"
                    if passed
                    else f"Sharpe {actual} < required {rule.min_sharpe}"
                ),
            )

        # max_drawdown
        if rule.max_drawdown is not None:
            actual = metrics.get("max_drawdown")
            passed = actual is not None and actual <= rule.max_drawdown
            criteria["max_drawdown"] = CriterionResult(
                passed=passed,
                required=rule.max_drawdown,
                actual=actual,
                detail=(
                    f"Drawdown {actual:.2%} <= {rule.max_drawdown:.2%}"
                    if passed
                    else f"Drawdown {actual} exceeds limit {rule.max_drawdown}"
                ),
            )

        # min_days_tested
        if rule.min_days_tested is not None:
            actual = metrics.get("days_tested")
            passed = actual is not None and actual >= rule.min_days_tested
            criteria["min_days_tested"] = CriterionResult(
                passed=passed,
                required=rule.min_days_tested,
                actual=actual,
                detail=(
                    f"{int(actual or 0)} days >= {rule.min_days_tested} required"
                    if passed
                    else f"Only {actual} days tested, need {rule.min_days_tested}"
                ),
            )

        # min_trade_count
        if rule.min_trade_count is not None:
            actual = metrics.get("trade_count")
            passed = actual is not None and actual >= rule.min_trade_count
            criteria["min_trade_count"] = CriterionResult(
                passed=passed,
                required=rule.min_trade_count,
                actual=actual,
                detail=(
                    f"{int(actual or 0)} trades >= {rule.min_trade_count} required"
                    if passed
                    else f"Only {actual} trades, need {rule.min_trade_count}"
                ),
            )

        # min_cagr
        if rule.min_cagr is not None:
            actual = metrics.get("cagr")
            passed = actual is not None and actual >= rule.min_cagr
            criteria["min_cagr"] = CriterionResult(
                passed=passed,
                required=rule.min_cagr,
                actual=actual,
                detail=(
                    f"CAGR {actual:.2%} >= {rule.min_cagr:.2%}"
                    if passed
                    else f"CAGR {actual} below required {rule.min_cagr}"
                ),
            )

        # min_win_rate
        if rule.min_win_rate is not None:
            actual = metrics.get("win_rate")
            passed = actual is not None and actual >= rule.min_win_rate
            criteria["min_win_rate"] = CriterionResult(
                passed=passed,
                required=rule.min_win_rate,
                actual=actual,
                detail=(
                    f"Win rate {actual:.2%} >= {rule.min_win_rate:.2%}"
                    if passed
                    else f"Win rate {actual} below required {rule.min_win_rate}"
                ),
            )

        is_eligible = all(c.passed for c in criteria.values())

        return PromotionEligibility(
            strategy_id=strategy_id,
            from_state=from_state,
            to_state=to_state,
            is_eligible=is_eligible,
            criteria_results=criteria,
        )

    @property
    def total_capital(self) -> float:
        return self._total_capital

    def update_total_capital(self, new_total: float) -> None:
        """
        Update total capital — call this when equity changes (e.g. after
        daily P&L settlement or a deposit/withdrawal).
        """
        if new_total < 0:
            raise ValueError(f"total_capital cannot be negative, got {new_total}")
        self._total_capital = new_total
