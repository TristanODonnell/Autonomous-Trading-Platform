from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)
from tests.application.services.test_quality_based_reallocation_service import _seed_policy


def _engine(session: Session) -> PortfolioEngine:
    return PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(session),
        overrides_repo=AllocationOverridesRepository(session),
        promotion_rules_repo=PromotionRulesRepository(session),
        total_capital=100_000.0,
    )


def test_cycle_budget_replaces_policy_pct_for_that_strategy(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.8)
    engine = _engine(db_session)
    engine.set_cycle_budgets({"alpha": 0.25})

    alpha = engine.get_allocation("alpha", GovernanceState.APPROVED_PAPER)
    other = engine.get_allocation("other", GovernanceState.APPROVED_PAPER)

    assert alpha.max_pct_of_capital == 0.25
    assert alpha.allocated_capital_usd == 25_000.0
    assert alpha.max_drawdown_allowed == 0.20  # other policy fields still apply
    assert other.max_pct_of_capital == 0.8


def test_zero_budget_allocates_nothing(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.8)
    engine = _engine(db_session)
    engine.set_cycle_budgets({"winding_down": 0.0})

    result = engine.get_allocation("winding_down", GovernanceState.APPROVED_PAPER)

    assert result.allocated_capital_usd == 0.0
