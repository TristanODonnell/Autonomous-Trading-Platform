from __future__ import annotations

from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import auth_headers, seed_strategy_governance


class TestSystemHealth:
    """
    GET /api/v1/system/health

    Verifies:
    - 200 with correct schema shape and field types
    - trading_mode reflects TRADING_ENVIRONMENT env var
    - active_strategy_count counts only approved_for_paper/live states
    - alerts list is populated correctly
    - endpoint is accessible without auth (public path consideration)
    - response time contract (lightweight)
    """

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers=auth_headers())
        assert response.status_code == 200

    def test_response_schema_shape(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers=auth_headers())
        body = response.json()

        # Envelope shape
        assert "data" in body
        assert "meta" in body
        assert body["error"] is None

        data = body["data"]
        assert "status" in data
        assert "trading_mode" in data
        assert "active_strategy_count" in data
        assert "alerts" in data

    def test_field_types(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]

        assert isinstance(data["status"], str)
        assert isinstance(data["trading_mode"], str)
        assert isinstance(data["active_strategy_count"], int)
        assert isinstance(data["alerts"], list)

    def test_trading_mode_reflects_env(self, client: TestClient) -> None:
        # conftest sets TRADING_ENVIRONMENT=simulation
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert data["trading_mode"] == "paper"

    def test_active_strategy_count_zero_when_no_strategies(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert data["active_strategy_count"] == 0

    def test_active_strategy_count_includes_paper_and_live(
        self, client: TestClient, db_session: Session
    ) -> None:
        seed_strategy_governance(db_session, state="approved_for_paper_trading")
        seed_strategy_governance(db_session, state="approved_for_live_trading")
        # These should NOT be counted
        seed_strategy_governance(db_session, state="proposed")
        seed_strategy_governance(db_session, state="rejected")

        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert data["active_strategy_count"] == 2

    def test_status_values_are_valid(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert data["status"] in {"healthy", "degraded", "critical"}

    def test_no_live_trading_flag_surfaces_as_alert(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NO_LIVE_TRADING", "true")
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert "live_trading_disabled" in data["alerts"]

    def test_shadow_mode_surfaces_as_alert(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHADOW_MODE_ENABLED", "true")
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert "shadow_mode_active" in data["alerts"]

    def test_healthy_status_when_no_alerts(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NO_LIVE_TRADING", "false")
        monkeypatch.setenv("SHADOW_MODE_ENABLED", "false")
        response = client.get("/api/v1/system/health", headers=auth_headers())
        data = response.json()["data"]
        assert data["status"] == "healthy"
        assert data["alerts"] == []

    def test_requires_auth(self, client: TestClient) -> None:
        # /api/v1/system/health is NOT in PUBLIC_PATHS — auth required
        response = client.get("/api/v1/system/health")
        assert response.status_code == 401

    def test_meta_fields_present(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers=auth_headers())
        meta = response.json()["meta"]
        assert "request_id" in meta
        assert "timestamp" in meta
        assert "version" in meta

    def test_responds_under_100ms(self, client: TestClient) -> None:
        start = perf_counter()
        response = client.get("/api/v1/system/health", headers=auth_headers())
        elapsed_ms = (perf_counter() - start) * 1000

        assert response.status_code == 200
        assert elapsed_ms < 100
