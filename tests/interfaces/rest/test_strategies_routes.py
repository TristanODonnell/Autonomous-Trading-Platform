from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from tests.conftest import auth_headers, seed_strategy_governance


def test_active_strategies_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/strategies/active")

    assert response.status_code == 401


def test_active_strategies_returns_dashboard_schema(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )
    db_session.add(
        StrategyConfigs(
            strategy_id="momentum_v1",
            config_hash="strategy-config-hash",
            config_json={},
            created_at=now,
            strategy_type="momentum",
            metadata_json={"display_name": "Momentum V1"},
        )
    )
    db_session.flush()

    response = client.get("/api/v1/strategies/active", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert "data" in body
    assert "meta" in body

    strategies = body["data"]["strategies"]
    assert len(strategies) == 1
    strategy = strategies[0]
    assert strategy["strategy_id"] == "momentum_v1"
    assert strategy["display_name"] == "Momentum V1"
    assert strategy["strategy_type"] == "momentum"
    assert strategy["status"] == "paper"
    assert isinstance(strategy["todays_return"], int | float | str)
    assert isinstance(strategy["trade_count_today"], int)
    assert isinstance(strategy["allocated_capital"], int | float | str)
    assert isinstance(strategy["enabled"], bool)


def test_strategy_allocation_requires_risk_manager_or_admin(client: TestClient) -> None:
    response = client.put(
        "/api/v1/strategies/momentum_v1/allocation",
        json={"allocated_capital": "25000", "reason": "risk rebalance"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 403


def test_strategy_allocation_override_updates_active_strategy_and_audit_log(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )
    db_session.add_all(
        [
            StrategyConfigs(
                strategy_id="momentum_v1",
                config_hash="strategy-config-hash",
                config_json={},
                created_at=now,
                strategy_type="momentum",
                metadata_json={"display_name": "Momentum V1"},
            ),
            CashSnapshot(
                snapshot_id=uuid4(),
                run_id=uuid4(),
                timestamp=now,
                currency="USD",
                cash=Decimal("100000"),
                buying_power=Decimal("100000"),
                reserved_cash=Decimal("0"),
                equity=Decimal("100000"),
                source=OrderSource.LEDGER,
                capital_bucket=None,
            ),
            AllocationOverrides(
                override_id="existing-override",
                strategy_id="momentum_v1",
                overridden_by="risk-manager-1",
                override_reason="previous override",
                max_pct_of_capital=None,
                max_position_size_usd=10000.0,
                max_drawdown_allowed=None,
                is_active=True,
                created_at=now,
                expires_at=None,
            ),
        ]
    )
    db_session.flush()

    response = client.put(
        "/api/v1/strategies/momentum_v1/allocation",
        json={"allocated_capital": "25000", "reason": "risk rebalance"},
        headers=auth_headers(role="risk_manager"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["strategy_id"] == "momentum_v1"
    assert Decimal(str(data["allocated_capital"])) == Decimal("25000")
    assert Decimal(str(data["total_portfolio_capital"])) == Decimal("100000.000000")
    assert data["reason"] == "risk rebalance"
    assert data["updated_by"] == "test-user"

    active_overrides = (
        db_session.query(AllocationOverrides)
        .filter(AllocationOverrides.strategy_id == "momentum_v1")
        .filter(AllocationOverrides.is_active.is_(True))
        .all()
    )
    assert len(active_overrides) == 1
    assert active_overrides[0].override_reason == "risk rebalance"
    assert active_overrides[0].max_position_size_usd == 25000.0

    audit_log = db_session.query(AuditLogRow).one()
    assert audit_log.event_type == "STRATEGY_ALLOCATION_OVERRIDDEN"
    assert audit_log.component == "strategies"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["actor"] == "test-user"
    assert audit_log.event_metadata["reason"] == "risk rebalance"
    assert audit_log.event_metadata["strategy_id"] == "momentum_v1"

    active_response = client.get("/api/v1/strategies/active", headers=auth_headers())
    active_strategy = active_response.json()["data"]["strategies"][0]
    assert Decimal(str(active_strategy["allocated_capital"])) == Decimal("25000.0")


def test_strategy_allocation_rejects_override_above_total_portfolio_capital(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )
    db_session.add(
        CashSnapshot(
            snapshot_id=uuid4(),
            run_id=uuid4(),
            timestamp=now,
            currency="USD",
            cash=Decimal("50000"),
            buying_power=Decimal("50000"),
            reserved_cash=Decimal("0"),
            equity=Decimal("50000"),
            source=OrderSource.LEDGER,
            capital_bucket=None,
        )
    )
    db_session.flush()

    response = client.put(
        "/api/v1/strategies/momentum_v1/allocation",
        json={"allocated_capital": "50001", "reason": "too large"},
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 409
    assert db_session.query(AllocationOverrides).all() == []
    assert db_session.query(AuditLogRow).all() == []


def test_strategy_enabled_requires_operator_or_admin(client: TestClient) -> None:
    response = client.put(
        "/api/v1/strategies/momentum_v1/enabled",
        json={"enabled": False, "reason": "operator pause"},
        headers=auth_headers(role="researcher"),
    )

    assert response.status_code == 403


def test_strategy_enabled_requires_non_empty_reason(client: TestClient) -> None:
    response = client.put(
        "/api/v1/strategies/momentum_v1/enabled",
        json={"enabled": False, "reason": "   "},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 422


def test_strategy_enabled_toggle_persists_state_audits_and_updates_active_response(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )
    db_session.add(
        StrategyConfigs(
            strategy_id="momentum_v1",
            config_hash="strategy-config-hash",
            config_json={},
            created_at=now,
            strategy_type="momentum",
            metadata_json={"display_name": "Momentum V1"},
        )
    )
    db_session.flush()

    response = client.put(
        "/api/v1/strategies/momentum_v1/enabled",
        json={"enabled": False, "reason": "operator pause"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["strategy_id"] == "momentum_v1"
    assert data["enabled"] is False
    assert data["status"] == "paper"
    assert data["reason"] == "operator pause"
    assert data["updated_by"] == "test-user"

    control_state = db_session.get(StrategyControlState, "momentum_v1")
    assert control_state is not None
    assert control_state.enabled is False
    assert control_state.reason == "operator pause"
    assert control_state.updated_by == "test-user"

    audit_log = db_session.query(AuditLogRow).one()
    assert audit_log.event_type == "STRATEGY_DISABLED"
    assert audit_log.component == "strategies"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["actor"] == "test-user"
    assert audit_log.event_metadata["reason"] == "operator pause"
    assert audit_log.event_metadata["strategy_id"] == "momentum_v1"
    assert audit_log.event_metadata["enabled"] is False

    active_response = client.get("/api/v1/strategies/active", headers=auth_headers())
    active_strategy = active_response.json()["data"]["strategies"][0]
    assert active_strategy["strategy_id"] == "momentum_v1"
    assert active_strategy["enabled"] is False


def test_strategy_enabled_can_reenable_approved_strategy(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )
    db_session.add(
        StrategyControlState(
            strategy_id="momentum_v1",
            enabled=False,
            reason="operator pause",
            updated_by="operator-1",
            updated_at=now,
        )
    )
    db_session.flush()

    response = client.put(
        "/api/v1/strategies/momentum_v1/enabled",
        json={"enabled": True, "reason": "operator resume"},
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["reason"] == "operator resume"

    control_state = db_session.get(StrategyControlState, "momentum_v1")
    assert control_state is not None
    assert control_state.enabled is True

    audit_log = db_session.query(AuditLogRow).one()
    assert audit_log.event_type == "STRATEGY_ENABLED"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["enabled"] is True


def test_strategy_enabled_rejects_enable_when_strategy_not_approved(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_strategy_governance(
        db_session,
        strategy_id="draft_v1",
        state="draft",
    )

    response = client.put(
        "/api/v1/strategies/draft_v1/enabled",
        json={"enabled": True, "reason": "try enable draft"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 409
    assert db_session.get(StrategyControlState, "draft_v1") is None
    assert db_session.query(AuditLogRow).all() == []


def test_strategy_governance_transition_promotes_research_to_paper_and_audits(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    source_run_id = uuid4()
    governance = seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_research",
    )
    governance.source_run_id = source_run_id
    db_session.add_all(
        [
            PromotionRules(
                rule_id="research-to-paper",
                from_status="approved_research",
                to_status="approved_paper",
                min_sharpe=1.0,
                max_drawdown=None,
                min_days_tested=None,
                min_trade_count=20,
                min_cagr=None,
                min_win_rate=None,
                is_active=True,
                created_at=now,
                notes=None,
            ),
            MetricsSummary(
                metrics_snapshot_id="metrics-1",
                run_id=str(source_run_id),
                created_at=now,
                total_return=0.12,
                sharpe_ratio=1.5,
                max_drawdown=-0.08,
                trade_count=42,
                winning_trade_count=25,
                losing_trade_count=17,
                volatility=0.2,
                metrics_json={},
            ),
        ]
    )
    db_session.flush()

    response = client.post(
        "/api/v1/strategies/momentum_v1/governance/transition",
        json={"to_state": "paper", "reason": "research passed validation"},
        headers=auth_headers(role="risk_manager"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["strategy_id"] == "momentum_v1"
    assert data["from_state"] == "approved_research"
    assert data["to_state"] == "approved_for_paper_trading"
    assert data["reason"] == "research passed validation"
    assert data["updated_by"] == "test-user"

    db_session.refresh(governance)
    assert governance.current_state == "approved_for_paper_trading"

    audit_log = db_session.query(AuditLogRow).one()
    assert audit_log.event_type == "STRATEGY_GOVERNANCE_TRANSITIONED"
    assert audit_log.component == "strategies"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["actor"] == "test-user"
    assert audit_log.event_metadata["reason"] == "research passed validation"
    assert audit_log.event_metadata["strategy_id"] == "momentum_v1"
    assert audit_log.event_metadata["from_state"] == "approved_research"
    assert audit_log.event_metadata["to_state"] == "approved_for_paper_trading"


def test_strategy_governance_transition_requires_target_state_role(
    client: TestClient,
    db_session: Session,
) -> None:
    governance = seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )

    response = client.post(
        "/api/v1/strategies/momentum_v1/governance/transition",
        json={"to_state": "live", "reason": "ready for live"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 403
    db_session.refresh(governance)
    assert governance.current_state == "approved_for_paper_trading"
    assert db_session.query(AuditLogRow).all() == []


def test_strategy_governance_transition_rejects_when_promotion_criteria_fail(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    source_run_id = uuid4()
    governance = seed_strategy_governance(
        db_session,
        strategy_id="momentum_v1",
        state="approved_for_paper_trading",
    )
    governance.source_run_id = source_run_id
    db_session.add_all(
        [
            PromotionRules(
                rule_id="paper-to-live",
                from_status="approved_paper",
                to_status="approved_live",
                min_sharpe=2.0,
                max_drawdown=None,
                min_days_tested=None,
                min_trade_count=10,
                min_cagr=None,
                min_win_rate=None,
                is_active=True,
                created_at=now,
                notes=None,
            ),
            MetricsSummary(
                metrics_snapshot_id="metrics-2",
                run_id=str(source_run_id),
                created_at=now,
                total_return=0.08,
                sharpe_ratio=1.1,
                max_drawdown=-0.12,
                trade_count=30,
                winning_trade_count=18,
                losing_trade_count=12,
                volatility=0.25,
                metrics_json={},
            ),
        ]
    )
    db_session.flush()

    response = client.post(
        "/api/v1/strategies/momentum_v1/governance/transition",
        json={"to_state": "live", "reason": "ready for live"},
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 409
    assert "Strategy does not meet promotion criteria" in response.json()["detail"]
    db_session.refresh(governance)
    assert governance.current_state == "approved_for_paper_trading"
    assert db_session.query(AuditLogRow).all() == []
