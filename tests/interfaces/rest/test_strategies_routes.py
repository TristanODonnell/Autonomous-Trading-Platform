from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.experiments import Experiments
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from tests.conftest import auth_headers, seed_strategy_governance


def _seed_story57_strategy(
    db_session: Session,
    *,
    strategy_id: str,
    state: str,
    enabled: bool = True,
    total_return: float = 0.10,
    sharpe_ratio: float = 1.2,
    max_drawdown: float = -0.08,
    trade_count: int = 20,
    winning_trade_count: int = 12,
    consistency_score: float = 0.7,
) -> None:
    now = datetime.now(UTC)
    config_hash = f"{strategy_id}-hash"
    db_session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=config_hash,
            config_json={"lookback": 20, "threshold": 1.5},
            created_at=now,
            strategy_type="momentum",
            metadata_json={"display_name": strategy_id.replace("_", " ").title()},
        )
    )
    db_session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=config_hash,
            current_state=state,
            experiment_id=f"{strategy_id}-experiment",
            source_run_id=None,
            submitted_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=1),
            submitted_by="researcher-1",
        )
    )
    if not enabled:
        db_session.add(
            StrategyControlState(
                strategy_id=strategy_id,
                enabled=False,
                reason="operator pause",
                updated_by="operator-1",
                updated_at=now,
            )
        )
    run_id = f"{strategy_id}-run"
    db_session.execute(
        text(
            """
            INSERT INTO simulation_runs (
                run_id,
                experiment_id,
                strategy_id,
                dataset_version,
                universe_version,
                price_basis,
                symbols,
                start_date,
                end_date,
                window_role,
                start_time,
                end_time,
                execution_config,
                status,
                metrics_snapshot_id
            )
            VALUES (
                :run_id,
                :experiment_id,
                :strategy_id,
                :dataset_version,
                :universe_version,
                :price_basis,
                :symbols,
                :start_date,
                :end_date,
                :window_role,
                :start_time,
                :end_time,
                :execution_config,
                :status,
                :metrics_snapshot_id
            )
            """
        ),
        {
            "run_id": run_id,
            "experiment_id": f"{strategy_id}-experiment",
            "strategy_id": strategy_id,
            "dataset_version": "dataset-v1",
            "universe_version": "universe-v1",
            "price_basis": "adjusted",
            "symbols": '["AAPL"]',
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 1, 3),
            "window_role": "test",
            "start_time": now - timedelta(days=3),
            "end_time": now - timedelta(days=1),
            "execution_config": "{}",
            "status": "complete",
            "metrics_snapshot_id": f"{strategy_id}-metrics",
        },
    )
    db_session.add(
        MetricsSummary(
            metrics_snapshot_id=f"{strategy_id}-metrics",
            run_id=run_id,
            created_at=now,
            total_return=total_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            trade_count=trade_count,
            winning_trade_count=winning_trade_count,
            losing_trade_count=trade_count - winning_trade_count,
            volatility=0.2,
            metrics_json={
                "win_rate": winning_trade_count / trade_count,
                "consistency_score": consistency_score,
                "equity_curve": [
                    {
                        "timestamp": "2026-01-03T00:00:00+00:00",
                        "equity": 103000.0,
                        "drawdown": 0.0,
                    },
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "equity": 100000.0,
                        "drawdown": 0.0,
                    },
                    {
                        "timestamp": "2026-01-02T00:00:00+00:00",
                        "equity": 101000.0,
                        "drawdown": -0.01,
                    },
                ],
            },
        )
    )
    db_session.flush()


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
        json={"allocation_pct": "25", "reason": "risk rebalance"},
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
        json={"allocation_pct": "25", "reason": "risk rebalance"},
        headers=auth_headers(role="risk_manager"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["strategy_id"] == "momentum_v1"
    assert Decimal(str(data["allocation_pct"])) == Decimal("25")
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
    assert active_overrides[0].max_position_size_usd is None
    assert active_overrides[0].max_pct_of_capital == 0.25

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
        json={"allocation_pct": "100.01", "reason": "too large"},
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 422
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


def test_strategy_list_returns_status_values_and_supports_filter(
    client: TestClient,
    db_session: Session,
) -> None:
    _seed_story57_strategy(
        db_session,
        strategy_id="live_v1",
        state="approved_for_live_trading",
    )
    _seed_story57_strategy(
        db_session,
        strategy_id="paper_v1",
        state="approved_for_paper_trading",
    )
    _seed_story57_strategy(
        db_session,
        strategy_id="research_v1",
        state="approved_research",
    )
    _seed_story57_strategy(
        db_session,
        strategy_id="disabled_v1",
        state="approved_for_live_trading",
        enabled=False,
    )

    response = client.get("/api/v1/strategies", headers=auth_headers())

    assert response.status_code == 200
    strategies = response.json()["data"]["strategies"]
    statuses = {strategy["strategy_id"]: strategy["status"] for strategy in strategies}
    assert statuses["live_v1"] == "live"
    assert statuses["paper_v1"] == "paper"
    assert statuses["research_v1"] == "research"
    assert statuses["disabled_v1"] == "off"

    filtered_response = client.get(
        "/api/v1/strategies?status=paper",
        headers=auth_headers(),
    )

    assert filtered_response.status_code == 200
    filtered = filtered_response.json()["data"]["strategies"]
    assert [strategy["strategy_id"] for strategy in filtered] == ["paper_v1"]


def test_strategy_detail_returns_metrics_configuration_and_history(
    client: TestClient,
    db_session: Session,
) -> None:
    _seed_story57_strategy(
        db_session,
        strategy_id="detail_v1",
        state="approved_for_paper_trading",
    )

    response = client.get("/api/v1/strategies/detail_v1", headers=auth_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["strategy_id"] == "detail_v1"
    assert data["metrics"]["total_return"] == 0.1
    assert "lookback: 20" in data["configuration_summary"]
    assert data["approval_status"] == "approved_for_paper_trading"
    assert data["deployment_history"] != []


def test_strategy_compare_accepts_two_to_five_ids(
    client: TestClient,
    db_session: Session,
) -> None:
    for index in range(5):
        _seed_story57_strategy(
            db_session,
            strategy_id=f"compare_{index}",
            state="approved_for_paper_trading",
            total_return=0.05 + index / 100,
            sharpe_ratio=1.0 + index / 10,
        )

    too_few = client.post(
        "/api/v1/strategies/compare",
        json={"strategy_ids": ["compare_0"]},
        headers=auth_headers(),
    )
    valid = client.post(
        "/api/v1/strategies/compare",
        json={"strategy_ids": [f"compare_{index}" for index in range(5)]},
        headers=auth_headers(),
    )
    too_many = client.post(
        "/api/v1/strategies/compare",
        json={"strategy_ids": [f"compare_{index}" for index in range(5)] + ["extra"]},
        headers=auth_headers(),
    )

    assert too_few.status_code == 422
    assert valid.status_code == 200
    assert len(valid.json()["data"]["rows"]) == 5
    assert valid.json()["data"]["metadata"]["total_return"]["best_strategy_id"] == "compare_4"
    assert too_many.status_code == 422


def test_strategy_equity_curve_data_is_ordered_chronologically(
    client: TestClient,
    db_session: Session,
) -> None:
    _seed_story57_strategy(
        db_session,
        strategy_id="curve_v1",
        state="approved_for_paper_trading",
    )

    response = client.get(
        "/api/v1/strategies/curve_v1/equity-curve",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    points = response.json()["data"]["points"]
    timestamps = [point["timestamp"] for point in points]
    assert timestamps == sorted(timestamps)
    assert [point["value"] for point in points] == [100000.0, 101000.0, 103000.0]


def test_experiment_creation_queues_job_and_returns_experiment_id(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post(
        "/api/v1/experiments",
        json={
            "name": "Momentum medium risk smoke",
            "experiment_type": "parameter_sweep",
            "symbols": ["SPY", "QQQ"],
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            "strategy_count": 50,
            "parameter_ranges": {"lookback": [20, 50]},
        },
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["experiment_id"]
    assert data["status"] == "pending"

    experiment = db_session.get(Experiments, data["experiment_id"])
    assert experiment is not None
    assert experiment.status == "pending"
    assert experiment.metadata_json is not None
    assert experiment.metadata_json["created_by"] == "test-user"
    assert experiment.metadata_json["experiment_type"] == "parameter_sweep"
    assert experiment.metadata_json["symbols"] == ["SPY", "QQQ"]
    assert experiment.metadata_json["strategy_count"] == 50
    assert experiment.metadata_json["parameter_ranges"] == {"lookback": [20, 50]}

    job = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == data["experiment_id"])
        .one()
    )
    assert job.status == "queued"
    assert job.job_name == "experiment_pipeline_cycle"


def test_experiment_list_and_detail_return_valid_shapes(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post(
        "/api/v1/experiments",
        json={
            "name": "Breakout low risk smoke",
            "experiment_type": "backtest",
            "symbols": ["SPY"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
        headers=auth_headers(role="admin"),
    )
    experiment_id = response.json()["data"]["experiment_id"]

    list_response = client.get("/api/v1/experiments", headers=auth_headers())
    detail_response = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers())

    assert list_response.status_code == 200
    assert list_response.json()["data"]["experiments"][0]["experiment_id"] == experiment_id
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["experiment_id"] == experiment_id
    assert detail["experiment_name"] == "Breakout low risk smoke"
    assert detail["experiment_type"] == "backtest"
    assert detail["status"] == "pending"
    assert detail["symbols"] == ["SPY"]
    assert detail["strategies"] == []
    assert detail["parameter_ranges"] == {}
    assert detail["price_basis"] == "RAW"
