from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.portfolio.exceptions import AllocationBudgetExceededError
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_strategy(session: Session, strategy_id: str) -> None:
    session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash="abc123",
            current_state="approved_for_live_trading",
            experiment_id="exp-1",
            source_run_id=None,
            submitted_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            submitted_by="test",
        )
    )
    session.flush()


def _seed_override(session: Session, strategy_id: str, max_pct: float) -> None:
    session.add(
        AllocationOverrides(
            override_id=str(uuid.uuid4()),
            strategy_id=strategy_id,
            overridden_by="test",
            override_reason="seeded",
            max_pct_of_capital=max_pct,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=datetime.now(UTC),
            expires_at=None,
        )
    )
    session.flush()


def _seed_policy(session: Session, approval_status: str, max_pct: float) -> None:
    session.add(
        CapitalAllocationPolicies(
            policy_id=str(uuid.uuid4()),
            approval_status=approval_status,
            performance_tier=None,
            max_pct_of_capital=max_pct,
            max_position_size_usd=None,
            max_drawdown_allowed=0.15,
            is_active=True,
            created_at=datetime.now(UTC),
        )
    )
    session.flush()


def _make_service(session: Session) -> StrategyAllocationService:
    return StrategyAllocationService(session=session)


# ---------------------------------------------------------------------------
# Budget enforcement tests
# ---------------------------------------------------------------------------


def test_override_within_budget_succeeds(db_session: Session) -> None:
    _seed_strategy(db_session, "strategy-a")
    _seed_strategy(db_session, "strategy-b")
    _seed_override(db_session, "strategy-b", 0.40)  # 40% already allocated

    svc = _make_service(db_session)
    result = svc.override_allocation(
        strategy_id="strategy-a",
        allocation_pct=Decimal("60"),  # 40 + 60 = 100% exactly → OK
        reason="test",
        updated_by="operator",
    )

    assert result.allocation_pct == Decimal("60")


def test_override_exactly_at_budget_succeeds(db_session: Session) -> None:
    _seed_strategy(db_session, "strategy-c")

    svc = _make_service(db_session)
    result = svc.override_allocation(
        strategy_id="strategy-c",
        allocation_pct=Decimal("100"),
        reason="test",
        updated_by="operator",
    )

    assert result.allocation_pct == Decimal("100")


def test_override_exceeds_budget_raises_error(db_session: Session) -> None:
    _seed_strategy(db_session, "strategy-x")
    _seed_strategy(db_session, "strategy-y")
    _seed_override(db_session, "strategy-x", 0.60)  # 60% already locked

    svc = _make_service(db_session)
    with pytest.raises(AllocationBudgetExceededError) as exc_info:
        svc.override_allocation(
            strategy_id="strategy-y",
            allocation_pct=Decimal("60"),  # 60 + 60 = 120% → BLOCKED
            reason="test",
            updated_by="operator",
        )

    err = exc_info.value
    assert err.strategy_id == "strategy-y"
    assert err.requested_pct == pytest.approx(0.60)
    assert err.resulting_total_pct == pytest.approx(1.20)
    assert err.configured_max_pct == pytest.approx(1.00)


def test_override_counts_policy_allocations_in_aggregate_budget(
    db_session: Session,
) -> None:
    _seed_policy(db_session, "approved_live", 0.50)
    _seed_strategy(db_session, "policy-strategy-a")
    _seed_strategy(db_session, "policy-strategy-b")

    svc = _make_service(db_session)
    with pytest.raises(AllocationBudgetExceededError) as exc_info:
        svc.override_allocation(
            strategy_id="policy-strategy-b",
            allocation_pct=Decimal("60"),
            reason="test",
            updated_by="operator",
        )

    assert exc_info.value.resulting_total_pct == pytest.approx(1.10)


def test_override_rollback_on_violation_does_not_persist(db_session: Session) -> None:
    _seed_strategy(db_session, "strategy-p")
    _seed_strategy(db_session, "strategy-q")
    _seed_override(db_session, "strategy-p", 0.70)

    svc = _make_service(db_session)
    with pytest.raises(AllocationBudgetExceededError):
        svc.override_allocation(
            strategy_id="strategy-q",
            allocation_pct=Decimal("50"),
            reason="test",
            updated_by="operator",
        )

    # No override should have been created for strategy-q
    remaining = (
        db_session.query(AllocationOverrides)
        .filter_by(strategy_id="strategy-q", is_active=True)
        .count()
    )
    assert remaining == 0


def test_override_violation_emits_audit_event(db_session: Session) -> None:
    _seed_strategy(db_session, "strategy-m")
    _seed_strategy(db_session, "strategy-n")
    _seed_override(db_session, "strategy-m", 0.80)

    svc = _make_service(db_session)
    with pytest.raises(AllocationBudgetExceededError):
        svc.override_allocation(
            strategy_id="strategy-n",
            allocation_pct=Decimal("40"),
            reason="testing audit",
            updated_by="risk-manager",
        )

    audit_rows = (
        db_session.query(AuditLogRow).filter_by(event_type="ALLOCATION_BUDGET_EXCEEDED").all()
    )
    assert len(audit_rows) == 1
    metadata = audit_rows[0].metadata_
    assert metadata["requested_strategy_id"] == "strategy-n"
    assert metadata["actor"] == "risk-manager"


def test_successful_override_includes_aggregate_in_audit_metadata(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "strategy-r")
    _seed_strategy(db_session, "strategy-s")
    _seed_override(db_session, "strategy-r", 0.30)

    svc = _make_service(db_session)
    svc.override_allocation(
        strategy_id="strategy-s",
        allocation_pct=Decimal("40"),
        reason="test",
        updated_by="operator",
    )

    audit_rows = (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_ALLOCATION_OVERRIDDEN").all()
    )
    assert len(audit_rows) == 1
    assert "resulting_aggregate_pct" in audit_rows[0].metadata_


def test_replacing_own_override_counts_correctly(db_session: Session) -> None:
    """Updating a strategy's own override should not double-count its old allocation."""
    _seed_strategy(db_session, "strategy-t")
    _seed_strategy(db_session, "strategy-u")
    _seed_override(db_session, "strategy-t", 0.60)  # existing 60% for strategy-t
    _seed_override(db_session, "strategy-u", 0.30)  # 30% for strategy-u

    svc = _make_service(db_session)
    # Updating strategy-t from 60% to 50%: projected = 30 + 50 = 80% → OK
    result = svc.override_allocation(
        strategy_id="strategy-t",
        allocation_pct=Decimal("50"),
        reason="reduce allocation",
        updated_by="operator",
    )
    assert result.allocation_pct == Decimal("50")


def test_replacing_own_override_blocked_when_still_over_budget(
    db_session: Session,
) -> None:
    """Even when updating own override, total must not exceed budget."""
    _seed_strategy(db_session, "strategy-v")
    _seed_strategy(db_session, "strategy-w")
    _seed_override(db_session, "strategy-v", 0.50)
    _seed_override(db_session, "strategy-w", 0.60)

    svc = _make_service(db_session)
    # strategy-v tries to go to 60%: projected = 60 + 60 = 120% → BLOCKED
    with pytest.raises(AllocationBudgetExceededError):
        svc.override_allocation(
            strategy_id="strategy-v",
            allocation_pct=Decimal("60"),
            reason="increase",
            updated_by="operator",
        )


def test_custom_threshold_allows_leverage(db_session: Session) -> None:
    """Operator can configure max > 1.0 to allow intentional leverage."""
    OperatorSettingsRepository(db_session).update_current(
        {"max_total_strategy_allocation_pct": 1.5},
        updated_by="test",
    )
    _seed_strategy(db_session, "strategy-lev-a")
    _seed_strategy(db_session, "strategy-lev-b")
    _seed_override(db_session, "strategy-lev-a", 0.80)

    svc = _make_service(db_session)
    # 80 + 60 = 140% < 150% configured threshold → OK
    result = svc.override_allocation(
        strategy_id="strategy-lev-b",
        allocation_pct=Decimal("60"),
        reason="leverage allowed",
        updated_by="operator",
    )
    assert result.allocation_pct == Decimal("60")


def test_disabled_strategy_allocation_is_excluded_from_aggregate_budget(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "disabled-strategy")
    _seed_strategy(db_session, "enabled-strategy")
    _seed_override(db_session, "disabled-strategy", 0.80)
    db_session.add(
        StrategyControlState(
            strategy_id="disabled-strategy",
            enabled=False,
            reason="paused",
            updated_by="operator",
            updated_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    svc = _make_service(db_session)
    result = svc.override_allocation(
        strategy_id="enabled-strategy",
        allocation_pct=Decimal("60"),
        reason="test",
        updated_by="operator",
    )

    assert result.allocation_pct == Decimal("60")


def test_inactive_strategy_allocation_is_excluded_from_aggregate_budget(
    db_session: Session,
) -> None:
    db_session.add(
        StrategyGovernance(
            strategy_id="research-strategy",
            config_hash="abc123",
            current_state="approved_research",
            experiment_id="exp-1",
            source_run_id=None,
            submitted_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            submitted_by="test",
        )
    )
    _seed_strategy(db_session, "live-strategy")
    _seed_override(db_session, "research-strategy", 0.80)

    svc = _make_service(db_session)
    result = svc.override_allocation(
        strategy_id="live-strategy",
        allocation_pct=Decimal("60"),
        reason="test",
        updated_by="operator",
    )

    assert result.allocation_pct == Decimal("60")


# ---------------------------------------------------------------------------
# PortfolioEngine.get_aggregate_allocation_pct() tests
# ---------------------------------------------------------------------------


def test_portfolio_engine_aggregate_allocation_pct(db_session: Session) -> None:
    from autonomous_trading_platform.governance.models.governance_state import GovernanceState
    from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
    from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
        CapitalAllocationPolicies,
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

    # Seed a policy for approved_live with 30% cap
    db_session.add(
        CapitalAllocationPolicies(
            policy_id=str(uuid.uuid4()),
            approval_status="approved_live",
            performance_tier=None,
            max_pct_of_capital=0.30,
            max_position_size_usd=None,
            max_drawdown_allowed=0.15,
            is_active=True,
            created_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    # Seed two strategies
    for sid in ["eng-strat-1", "eng-strat-2"]:
        _seed_strategy(db_session, sid)

    # Override eng-strat-1 with 40%
    _seed_override(db_session, "eng-strat-1", 0.40)

    engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(db_session),
        overrides_repo=AllocationOverridesRepository(db_session),
        promotion_rules_repo=PromotionRulesRepository(db_session),
        total_capital=100_000.0,
    )

    entries: list[tuple[str, GovernanceState, str | None]] = [
        ("eng-strat-1", GovernanceState.APPROVED_LIVE, None),
        ("eng-strat-2", GovernanceState.APPROVED_LIVE, None),
    ]
    aggregate = engine.get_aggregate_allocation_pct(entries)

    # eng-strat-1 has override 40%, eng-strat-2 uses policy 30% → total 70%
    assert aggregate == pytest.approx(0.70)


def test_portfolio_engine_aggregate_empty_entries(db_session: Session) -> None:
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

    engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(db_session),
        overrides_repo=AllocationOverridesRepository(db_session),
        promotion_rules_repo=PromotionRulesRepository(db_session),
        total_capital=100_000.0,
    )

    assert engine.get_aggregate_allocation_pct([]) == pytest.approx(0.0)
