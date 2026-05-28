from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.factor_neutralization_service import (
    FactorNeutralizationConfig,
    FactorNeutralizationService,
)
from autonomous_trading_platform.contracts.runtime.factor_neutralization import (
    FactorNeutralizationMode,
    FactorNeutralizationRequest,
)
from tests.conftest import auth_headers


def test_factor_neutralization_config_current_and_history_routes(
    client: TestClient,
    db_session: Session,
) -> None:
    FactorNeutralizationService(
        session=db_session,
        config=FactorNeutralizationConfig(enabled=True, mode=FactorNeutralizationMode.OBSERVE_ONLY),
    ).neutralize(
        request=FactorNeutralizationRequest(
            assets=["a", "b"],
            current_weights={"a": 0.7, "b": 0.3},
            factor_exposures={
                "a": {"market_beta": 1.2, "momentum": 0.2},
                "b": {"market_beta": -0.2, "momentum": -0.1},
            },
            portfolio_id="paper",
        ),
        run_id="api-neutral-run",
    )

    config = client.get("/api/v1/portfolio/factor-neutralization/config", headers=auth_headers())
    assert config.status_code == 200
    assert "constraints" in config.json()["data"]

    current = client.get(
        "/api/v1/portfolio/factor-neutralization/current?portfolio_id=paper",
        headers=auth_headers(),
    )
    assert current.status_code == 200
    assert current.json()["data"]["run_id"] == "api-neutral-run"

    history = client.get(
        "/api/v1/portfolio/factor-neutralization/history?portfolio_id=paper",
        headers=auth_headers(),
    )
    assert history.status_code == 200
    assert history.json()["data"]["runs"][0]["run_id"] == "api-neutral-run"
