"""
Tests for FINDING-13: stale CashSnapshot rejection during allocation.

Freshness checks are gated on app_env not in {"test"}, so these tests inject a
custom settings object with app_env="paper" to activate the check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.portfolio.exceptions import (
    MissingCapitalDataError,
    StaleCapitalDataError,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import (
    CashSnapshot as CashSnapshotRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)

# ---------------------------------------------------------------------------
# Minimal settings stub — avoids env-var / broker validation in Settings()
# ---------------------------------------------------------------------------


@dataclass
class _Settings:
    initial_capital: float = 10_000.0
    trading_environment: TradingEnvironment = TradingEnvironment.PAPER
    app_env: str = "paper"
    max_cash_snapshot_age_seconds: int = 900
    enable_cash_snapshot_freshness_check: bool = True


def _fresh_settings(**overrides) -> _Settings:
    s = _Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------


def _seed_cash_snapshot(
    session: Session,
    *,
    equity: Decimal,
    timestamp: datetime,
) -> CashSnapshotRow:
    row = CashSnapshotRow(
        snapshot_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        timestamp=timestamp,
        currency="USD",
        cash=Decimal("50000"),
        buying_power=Decimal("50000"),
        reserved_cash=Decimal("0"),
        equity=equity,
        source=OrderSource.LEDGER,
    )
    session.add(row)
    session.flush()
    return row


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


def _make_service(session: Session, settings: _Settings | None = None) -> StrategyAllocationService:
    return StrategyAllocationService(
        session=session,
        settings=cast(Settings, settings or _fresh_settings()),
    )


# ---------------------------------------------------------------------------
# Fresh snapshot — allocation proceeds normally
# ---------------------------------------------------------------------------


def test_fresh_snapshot_allows_allocation(db_session: Session) -> None:
    now = datetime.now(UTC)
    _seed_cash_snapshot(db_session, equity=Decimal("100000"), timestamp=now - timedelta(seconds=30))
    _seed_strategy(db_session, "strat-fresh")
    _seed_override(db_session, "strat-fresh", 0.20)

    svc = _make_service(db_session)
    result = svc.override_allocation(
        strategy_id="strat-fresh",
        allocation_pct=Decimal("20"),
        reason="test",
        updated_by="op",
    )

    assert result.allocation_pct == Decimal("20")
    assert result.total_portfolio_capital == Decimal("100000")


# ---------------------------------------------------------------------------
# Stale snapshot — raises StaleCapitalDataError
# ---------------------------------------------------------------------------


def test_stale_snapshot_raises_error(db_session: Session) -> None:
    old_ts = datetime.now(UTC) - timedelta(seconds=1800)
    _seed_cash_snapshot(db_session, equity=Decimal("100000"), timestamp=old_ts)
    _seed_strategy(db_session, "strat-stale")

    svc = _make_service(db_session, settings=_fresh_settings(max_cash_snapshot_age_seconds=900))
    with pytest.raises(StaleCapitalDataError) as exc_info:
        svc.override_allocation(
            strategy_id="strat-stale",
            allocation_pct=Decimal("10"),
            reason="test",
            updated_by="op",
        )

    err = exc_info.value
    assert err.age_seconds > 900
    assert err.max_age_seconds == 900
    assert err.snapshot_id is not None


def test_stale_snapshot_emits_audit_event(db_session: Session) -> None:
    old_ts = datetime.now(UTC) - timedelta(seconds=2000)
    _seed_cash_snapshot(db_session, equity=Decimal("50000"), timestamp=old_ts)
    _seed_strategy(db_session, "strat-audit-stale")

    svc = _make_service(db_session, settings=_fresh_settings(max_cash_snapshot_age_seconds=600))
    with pytest.raises(StaleCapitalDataError):
        svc.override_allocation(
            strategy_id="strat-audit-stale",
            allocation_pct=Decimal("10"),
            reason="test",
            updated_by="op",
        )

    rows = db_session.query(AuditLogRow).filter_by(event_type="CASH_SNAPSHOT_STALE").all()
    assert len(rows) == 1
    meta = rows[0].metadata_
    assert "cash_snapshot_id" in meta
    assert meta["snapshot_age_seconds"] > 600
    assert meta["max_allowed_age_seconds"] == 600
    assert meta["trading_environment"] == "paper"


# ---------------------------------------------------------------------------
# Missing snapshot — fails closed in live/paper, falls back in test mode
# ---------------------------------------------------------------------------


def test_missing_snapshot_fails_closed_in_live_paper(db_session: Session) -> None:
    # No snapshot seeded — only initial_capital_config fallback would apply in test mode
    _seed_strategy(db_session, "strat-no-snap")

    svc = _make_service(db_session, settings=_fresh_settings())
    with pytest.raises(MissingCapitalDataError) as exc_info:
        svc.override_allocation(
            strategy_id="strat-no-snap",
            allocation_pct=Decimal("10"),
            reason="test",
            updated_by="op",
        )

    assert exc_info.value.trading_environment == "paper"


def test_missing_snapshot_emits_audit_event(db_session: Session) -> None:
    _seed_strategy(db_session, "strat-missing-audit")

    svc = _make_service(db_session, settings=_fresh_settings())
    with pytest.raises(MissingCapitalDataError):
        svc.override_allocation(
            strategy_id="strat-missing-audit",
            allocation_pct=Decimal("5"),
            reason="test",
            updated_by="op",
        )

    rows = db_session.query(AuditLogRow).filter_by(event_type="MISSING_CAPITAL_DATA").all()
    assert len(rows) == 1
    assert rows[0].metadata_["trading_environment"] == "paper"


def test_test_mode_falls_back_to_initial_capital_when_no_snapshot(db_session: Session) -> None:
    # app_env="test" — freshness check bypassed, initial_capital used
    _seed_strategy(db_session, "strat-test-fallback")
    test_settings = _fresh_settings(
        app_env="test",
        initial_capital=25_000.0,
        enable_cash_snapshot_freshness_check=True,  # enabled but gated by app_env
    )

    svc = _make_service(db_session, settings=test_settings)
    result = svc.override_allocation(
        strategy_id="strat-test-fallback",
        allocation_pct=Decimal("50"),
        reason="test",
        updated_by="op",
    )

    assert result.total_portfolio_capital == Decimal("25000")


# ---------------------------------------------------------------------------
# AllocationResult snapshot metadata fields
# ---------------------------------------------------------------------------


def test_allocation_result_includes_snapshot_metadata(db_session: Session) -> None:
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

    snap_ts = datetime.now(UTC) - timedelta(seconds=10)
    snap = _seed_cash_snapshot(db_session, equity=Decimal("80000"), timestamp=snap_ts)
    _seed_policy(db_session, "approved_live", 0.25)
    _seed_strategy(db_session, "strat-result-meta")

    engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(db_session),
        overrides_repo=AllocationOverridesRepository(db_session),
        promotion_rules_repo=PromotionRulesRepository(db_session),
        total_capital=80_000.0,
        cash_snapshot_id=str(snap.snapshot_id),
        cash_snapshot_as_of=snap_ts,
        snapshot_age_seconds=10.0,
        capital_source="cash_snapshot",
    )

    result = engine.get_allocation(
        strategy_id="strat-result-meta",
        approval_status=GovernanceState.APPROVED_LIVE,
    )

    assert result.cash_snapshot_id == str(snap.snapshot_id)
    assert result.cash_snapshot_as_of == snap_ts
    assert result.snapshot_age_seconds == pytest.approx(10.0)
    assert result.capital_source == "cash_snapshot"


def test_allocation_result_snapshot_metadata_none_when_using_config_fallback(
    db_session: Session,
) -> None:
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

    _seed_policy(db_session, "approved_live", 0.30)
    _seed_strategy(db_session, "strat-no-meta")

    engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(db_session),
        overrides_repo=AllocationOverridesRepository(db_session),
        promotion_rules_repo=PromotionRulesRepository(db_session),
        total_capital=10_000.0,
        capital_source="initial_capital_config",
    )

    result = engine.get_allocation(
        strategy_id="strat-no-meta",
        approval_status=GovernanceState.APPROVED_LIVE,
    )

    assert result.cash_snapshot_id is None
    assert result.cash_snapshot_as_of is None
    assert result.snapshot_age_seconds is None
    assert result.capital_source == "initial_capital_config"


# ---------------------------------------------------------------------------
# Freshness check disabled via flag — always allows (with snapshot present)
# ---------------------------------------------------------------------------


def test_freshness_check_disabled_allows_stale_snapshot(db_session: Session) -> None:
    old_ts = datetime.now(UTC) - timedelta(hours=5)
    _seed_cash_snapshot(db_session, equity=Decimal("60000"), timestamp=old_ts)
    _seed_strategy(db_session, "strat-disabled-check")

    settings = _fresh_settings(
        max_cash_snapshot_age_seconds=900,
        enable_cash_snapshot_freshness_check=False,
    )
    svc = _make_service(db_session, settings=settings)
    result = svc.override_allocation(
        strategy_id="strat-disabled-check",
        allocation_pct=Decimal("20"),
        reason="test",
        updated_by="op",
    )

    assert result.allocation_pct == Decimal("20")
    assert result.total_portfolio_capital == Decimal("60000")


# ---------------------------------------------------------------------------
# Live mode uses tighter default (600s)
# ---------------------------------------------------------------------------


def test_live_mode_rejects_snapshot_older_than_600s(db_session: Session) -> None:
    old_ts = datetime.now(UTC) - timedelta(seconds=700)
    _seed_cash_snapshot(db_session, equity=Decimal("200000"), timestamp=old_ts)
    _seed_strategy(db_session, "strat-live-tight")

    settings = _fresh_settings(
        trading_environment=TradingEnvironment.LIVE,
        max_cash_snapshot_age_seconds=600,
    )
    svc = _make_service(db_session, settings=settings)
    with pytest.raises(StaleCapitalDataError) as exc_info:
        svc.override_allocation(
            strategy_id="strat-live-tight",
            allocation_pct=Decimal("10"),
            reason="test",
            updated_by="op",
        )

    assert exc_info.value.max_age_seconds == 600


def test_live_mode_allows_snapshot_within_600s(db_session: Session) -> None:
    recent_ts = datetime.now(UTC) - timedelta(seconds=300)
    _seed_cash_snapshot(db_session, equity=Decimal("200000"), timestamp=recent_ts)
    _seed_strategy(db_session, "strat-live-ok")

    settings = _fresh_settings(
        trading_environment=TradingEnvironment.LIVE,
        max_cash_snapshot_age_seconds=600,
    )
    svc = _make_service(db_session, settings=settings)
    result = svc.override_allocation(
        strategy_id="strat-live-ok",
        allocation_pct=Decimal("10"),
        reason="test",
        updated_by="op",
    )

    assert result.allocation_pct == Decimal("10")
