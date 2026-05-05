from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
)


@dataclass
class FakePositionSnapshot:
    timestamp: datetime
    snapshot_id: str


@dataclass
class FakeCashSnapshot:
    timestamp: datetime
    cash: Decimal


class FakePortfolioSummaryRepository:
    def __init__(self) -> None:
        self.latest_pos: FakePositionSnapshot | None = FakePositionSnapshot(
            snapshot_id="latest",
            timestamp=datetime(2026, 5, 5, 16, 0, tzinfo=UTC),
        )
        self.prior_pos: FakePositionSnapshot | None = FakePositionSnapshot(
            snapshot_id="prior",
            timestamp=datetime(2026, 5, 4, 16, 0, tzinfo=UTC),
        )
        self.first_pos: FakePositionSnapshot | None = FakePositionSnapshot(
            snapshot_id="first",
            timestamp=datetime(2026, 1, 1, 16, 0, tzinfo=UTC),
        )

        self.cash_by_snapshot = {
            "latest": FakeCashSnapshot(timestamp=self.latest_pos.timestamp, cash=Decimal("2000")),
            "prior": FakeCashSnapshot(timestamp=self.prior_pos.timestamp, cash=Decimal("1500")),
            "first": FakeCashSnapshot(timestamp=self.first_pos.timestamp, cash=Decimal("1000")),
        }

        self.equity_by_snapshot = {
            "latest": Decimal("110000"),
            "prior": Decimal("100000"),
            "first": Decimal("90000"),
        }

    def get_latest_position_snapshot(self):
        return self.latest_pos

    def get_prior_position_snapshot(self, before_timestamp):
        return self.prior_pos

    def get_first_position_snapshot(self):
        return self.first_pos

    def get_cash_snapshot_at_or_before(self, timestamp):
        for pos in [self.latest_pos, self.prior_pos, self.first_pos]:
            if pos is None:
                continue

            if pos.timestamp == timestamp:
                return self.cash_by_snapshot.get(pos.snapshot_id)

        return None

    def compute_total_equity(self, position_snapshot, cash_snapshot):
        return self.equity_by_snapshot[position_snapshot.snapshot_id]


def test_calculates_portfolio_summary_values() -> None:
    repo = FakePortfolioSummaryRepository()
    service = PortfolioSummaryService(session=None, repo=repo)

    result = service.get_summary()

    assert result["current_portfolio_value"] == Decimal("110000")
    assert result["todays_pnl_amount"] == Decimal("10000")
    assert result["todays_pnl_percent"] == Decimal("0.10")
    assert result["total_pnl_amount"] == Decimal("20000")
    assert result["total_pnl_percent"] == Decimal("0.2222222222222222222222222222")
    assert result["cash_balance"] == Decimal("2000")


def test_returns_empty_summary_when_latest_position_missing() -> None:
    repo = FakePortfolioSummaryRepository()
    repo.latest_pos = None
    service = PortfolioSummaryService(session=None, repo=repo)

    result = service.get_summary()

    assert result["current_portfolio_value"] == Decimal("0.00")
    assert result["todays_pnl_amount"] == Decimal("0.00")
    assert result["todays_pnl_percent"] == Decimal("0.00")
    assert result["total_pnl_amount"] == Decimal("0.00")
    assert result["total_pnl_percent"] == Decimal("0.00")
    assert result["cash_balance"] == Decimal("0.00")


def test_returns_empty_summary_when_latest_cash_missing() -> None:
    repo = FakePortfolioSummaryRepository()
    repo.cash_by_snapshot.pop("latest")
    service = PortfolioSummaryService(session=None, repo=repo)

    result = service.get_summary()

    assert result["current_portfolio_value"] == Decimal("0.00")
    assert result["cash_balance"] == Decimal("0.00")


def test_uses_current_value_when_prior_snapshot_missing() -> None:
    repo = FakePortfolioSummaryRepository()
    repo.prior_pos = None
    service = PortfolioSummaryService(session=None, repo=repo)

    result = service.get_summary()

    assert result["current_portfolio_value"] == Decimal("110000")
    assert result["todays_pnl_amount"] == Decimal("0")
    assert result["todays_pnl_percent"] == Decimal("0")


def test_uses_current_value_when_first_snapshot_missing() -> None:
    repo = FakePortfolioSummaryRepository()
    repo.first_pos = None
    service = PortfolioSummaryService(session=None, repo=repo)

    result = service.get_summary()

    assert result["current_portfolio_value"] == Decimal("110000")
    assert result["total_pnl_amount"] == Decimal("0")
    assert result["total_pnl_percent"] == Decimal("0")


def test_percent_change_returns_zero_when_base_is_zero() -> None:
    repo = FakePortfolioSummaryRepository()
    repo.equity_by_snapshot["prior"] = Decimal("0")
    service = PortfolioSummaryService(session=None, repo=repo)

    result = service.get_summary()

    assert result["todays_pnl_amount"] == Decimal("110000")
    assert result["todays_pnl_percent"] == Decimal("0")
