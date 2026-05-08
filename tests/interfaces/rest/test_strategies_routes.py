from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
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
