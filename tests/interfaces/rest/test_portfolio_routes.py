from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from tests.conftest import auth_headers


def seed_portfolio_state(db_session: Session) -> None:
    run_id = uuid4()
    prior_snapshot_id = uuid4()
    latest_snapshot_id = uuid4()
    prior_timestamp = datetime.now(UTC) - timedelta(days=1)
    latest_timestamp = datetime.now(UTC)
    prior_cash_timestamp = prior_timestamp - timedelta(seconds=1)
    latest_cash_timestamp = latest_timestamp - timedelta(seconds=1)

    db_session.add_all(
        [
            PositionSnapshot(
                snapshot_id=prior_snapshot_id,
                run_id=run_id,
                timestamp=prior_timestamp,
                source=OrderSource.LEDGER,
            ),
            PositionSnapshotItem(
                snapshot_id=prior_snapshot_id,
                symbol="AAPL",
                quantity=Decimal("10"),
                avg_cost=Decimal("100"),
                market_price=Decimal("5800"),
                market_value=Decimal("58000"),
                unrealized_pnl=Decimal("1000"),
            ),
            CashSnapshot(
                snapshot_id=uuid4(),
                run_id=run_id,
                timestamp=prior_cash_timestamp,
                currency="USD",
                cash=Decimal("50000"),
                buying_power=Decimal("50000"),
                reserved_cash=Decimal("0"),
                equity=Decimal("108000"),
                source=OrderSource.LEDGER,
                capital_bucket=None,
            ),
            PositionSnapshot(
                snapshot_id=latest_snapshot_id,
                run_id=run_id,
                timestamp=latest_timestamp,
                source=OrderSource.LEDGER,
            ),
            PositionSnapshotItem(
                snapshot_id=latest_snapshot_id,
                symbol="AAPL",
                quantity=Decimal("10"),
                avg_cost=Decimal("100"),
                market_price=Decimal("6000"),
                market_value=Decimal("60000"),
                unrealized_pnl=Decimal("3000"),
            ),
            CashSnapshot(
                snapshot_id=uuid4(),
                run_id=run_id,
                timestamp=latest_cash_timestamp,
                currency="USD",
                cash=Decimal("50000"),
                buying_power=Decimal("50000"),
                reserved_cash=Decimal("0"),
                equity=Decimal("110000"),
                source=OrderSource.LEDGER,
                capital_bucket=None,
            ),
        ]
    )
    db_session.flush()


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

    def test_returns_seeded_portfolio_values(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        seed_portfolio_state(db_session)

        response = client.get("/api/v1/portfolio/summary", headers=auth_headers())

        assert response.status_code == 200
        data = response.json()["data"]
        assert Decimal(str(data["current_portfolio_value"])) == Decimal("110000.000000")
        assert isinstance(data["todays_pnl_amount"], int | float | str)
        assert isinstance(data["todays_pnl_percent"], int | float | str)
        assert Decimal(str(data["cash_balance"])) == Decimal("50000.000000")


class TestPortfolioEquityCurve:
    """
    GET /api/v1/portfolio/equity-curve

    Verifies:
    - endpoint requires auth
    - response uses envelope shape
    - response exposes chartable time series points
    - invalid periods are rejected
    - no internal metadata leaks through the public API
    """

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/portfolio/equity-curve?period=1w",
            headers=auth_headers(),
        )

        assert response.status_code == 200

    def test_response_schema_shape(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/portfolio/equity-curve?period=1w",
            headers=auth_headers(),
        )
        body = response.json()

        assert "data" in body
        assert "meta" in body
        assert body["error"] is None

        data = body["data"]
        assert "period" in data
        assert "points" in data
        assert data["period"] == "1w"
        assert isinstance(data["points"], list)

    def test_point_shape_when_points_exist(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/portfolio/equity-curve?period=1w",
            headers=auth_headers(),
        )
        points = response.json()["data"]["points"]

        for point in points:
            assert "timestamp" in point
            assert "value" in point
            assert isinstance(point["timestamp"], str)
            assert isinstance(point["value"], int | float | str)

    def test_requires_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/portfolio/equity-curve?period=1w")

        assert response.status_code == 401

    def test_rejects_invalid_period(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/portfolio/equity-curve?period=all",
            headers=auth_headers(),
        )

        assert response.status_code == 422

    def test_meta_fields_present(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/portfolio/equity-curve?period=1w",
            headers=auth_headers(),
        )
        meta = response.json()["meta"]

        assert "request_id" in meta
        assert "timestamp" in meta
        assert "version" in meta

    def test_does_not_expose_internal_fields(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/portfolio/equity-curve?period=1w",
            headers=auth_headers(),
        )
        data = response.json()["data"]

        forbidden_fields = {
            "dataset_version",
            "run_id",
            "experiment_id",
            "strategy_id",
            "price_basis",
            "snapshot_id",
            "source",
            "currency",
            "buying_power",
            "reserved_cash",
            "capital_bucket",
        }

        for field in forbidden_fields:
            assert field not in data

        for point in data["points"]:
            for field in forbidden_fields:
                assert field not in point

    def test_returns_seeded_equity_curve_points(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        seed_portfolio_state(db_session)

        response = client.get(
            "/api/v1/portfolio/equity-curve?period=1w",
            headers=auth_headers(),
        )

        assert response.status_code == 200
        points = response.json()["data"]["points"]
        assert len(points) == 2
        assert [Decimal(str(point["value"])) for point in points] == [
            Decimal("108000.000000"),
            Decimal("110000.000000"),
        ]
