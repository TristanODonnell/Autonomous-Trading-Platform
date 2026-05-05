from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
    PortfolioEquityCurveService,
)


@dataclass
class FakeCashSnapshot:
    timestamp: datetime
    equity: Decimal | None


class FakeCashSnapshotRepository:
    def __init__(self) -> None:
        self.received_start_timestamp: datetime | None = None
        self.snapshots = [
            FakeCashSnapshot(
                timestamp=datetime(2026, 5, 1, 16, 0, tzinfo=UTC),
                equity=Decimal("100000"),
            ),
            FakeCashSnapshot(
                timestamp=datetime(2026, 5, 2, 16, 0, tzinfo=UTC),
                equity=Decimal("101500"),
            ),
            FakeCashSnapshot(
                timestamp=datetime(2026, 5, 3, 16, 0, tzinfo=UTC),
                equity=Decimal("103000"),
            ),
        ]

    def list_since(self, start_timestamp: datetime):
        self.received_start_timestamp = start_timestamp
        return self.snapshots


def test_returns_equity_curve_points() -> None:
    repo = FakeCashSnapshotRepository()
    service = PortfolioEquityCurveService(session=None, repo=repo)

    result = service.get_equity_curve(period="1w")

    assert result["period"] == "1w"
    assert result["points"] == [
        {
            "timestamp": datetime(2026, 5, 1, 16, 0, tzinfo=UTC),
            "value": Decimal("100000"),
        },
        {
            "timestamp": datetime(2026, 5, 2, 16, 0, tzinfo=UTC),
            "value": Decimal("101500"),
        },
        {
            "timestamp": datetime(2026, 5, 3, 16, 0, tzinfo=UTC),
            "value": Decimal("103000"),
        },
    ]


def test_filters_out_snapshots_without_equity() -> None:
    repo = FakeCashSnapshotRepository()
    repo.snapshots = [
        FakeCashSnapshot(
            timestamp=datetime(2026, 5, 1, 16, 0, tzinfo=UTC),
            equity=Decimal("100000"),
        ),
        FakeCashSnapshot(
            timestamp=datetime(2026, 5, 2, 16, 0, tzinfo=UTC),
            equity=None,
        ),
    ]
    service = PortfolioEquityCurveService(session=None, repo=repo)

    result = service.get_equity_curve(period="1w")

    assert result["points"] == [
        {
            "timestamp": datetime(2026, 5, 1, 16, 0, tzinfo=UTC),
            "value": Decimal("100000"),
        },
    ]


def test_returns_empty_points_when_no_snapshots_exist() -> None:
    repo = FakeCashSnapshotRepository()
    repo.snapshots = []
    service = PortfolioEquityCurveService(session=None, repo=repo)

    result = service.get_equity_curve(period="1w")

    assert result["period"] == "1w"
    assert result["points"] == []


def test_passes_resolved_start_timestamp_to_repository() -> None:
    repo = FakeCashSnapshotRepository()
    service = PortfolioEquityCurveService(session=None, repo=repo)

    service.get_equity_curve(period="1w")

    assert repo.received_start_timestamp is not None
    assert repo.received_start_timestamp.tzinfo is UTC


def test_invalid_period_raises_value_error() -> None:
    repo = FakeCashSnapshotRepository()
    service = PortfolioEquityCurveService(session=None, repo=repo)

    with pytest.raises(ValueError, match="Invalid period"):
        service.get_equity_curve(period="all")
