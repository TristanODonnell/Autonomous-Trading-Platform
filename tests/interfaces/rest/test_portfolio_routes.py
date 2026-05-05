from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers


class TestPortfolioSummary:
    """
    GET /api/v1/portfolio/summary

    Verifies:
    - endpoint requires auth
    - response uses envelope shape
    - response exposes only financial fields
    - no internal metadata leaks through the public API
    """

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/summary", headers=auth_headers())

        assert response.status_code == 200

    def test_response_schema_shape(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/summary", headers=auth_headers())
        body = response.json()

        assert "data" in body
        assert "meta" in body
        assert body["error"] is None

        data = body["data"]
        assert "current_portfolio_value" in data
        assert "todays_pnl_amount" in data
        assert "todays_pnl_percent" in data
        assert "total_pnl_amount" in data
        assert "total_pnl_percent" in data
        assert "cash_balance" in data

    def test_does_not_expose_internal_fields(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/summary", headers=auth_headers())
        data = response.json()["data"]

        forbidden_fields = {
            "dataset_version",
            "run_id",
            "experiment_id",
            "strategy_id",
            "price_basis",
            "snapshot_id",
        }

        for field in forbidden_fields:
            assert field not in data

    def test_field_types(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/summary", headers=auth_headers())
        data = response.json()["data"]

        assert isinstance(data["current_portfolio_value"], int | float | str)
        assert isinstance(data["todays_pnl_amount"], int | float | str)
        assert isinstance(data["todays_pnl_percent"], int | float | str)
        assert isinstance(data["total_pnl_amount"], int | float | str)
        assert isinstance(data["total_pnl_percent"], int | float | str)
        assert isinstance(data["cash_balance"], int | float | str)

    def test_requires_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/summary")

        assert response.status_code == 401

    def test_meta_fields_present(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/summary", headers=auth_headers())
        meta = response.json()["meta"]

        assert "request_id" in meta
        assert "timestamp" in meta
        assert "version" in meta
