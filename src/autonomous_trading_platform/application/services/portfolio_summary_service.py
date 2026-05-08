from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.repositories.queries.portfolio_summary_repository import (
    PortfolioSummaryRepository,
)


class PortfolioSummaryReader(Protocol):
    def get_latest_position_snapshot(self) -> PositionSnapshot | None: ...

    def get_prior_position_snapshot(
        self,
        before_timestamp: datetime,
    ) -> PositionSnapshot | None: ...

    def get_first_position_snapshot(self) -> PositionSnapshot | None: ...

    def get_cash_snapshot_at_or_before(
        self,
        timestamp: datetime,
    ) -> CashSnapshot | None: ...

    def compute_total_equity(
        self,
        position_snapshot: PositionSnapshot,
        cash_snapshot: CashSnapshot,
    ) -> Decimal: ...


class PortfolioSummaryService:
    def __init__(
        self,
        session: Session,
        repo: PortfolioSummaryReader | None = None,
    ) -> None:
        self.session = session
        self.repo = repo or PortfolioSummaryRepository(session=session)

    def get_summary(self) -> dict:
        latest_pos = self.repo.get_latest_position_snapshot()
        if latest_pos is None:
            return self._empty_summary()

        latest_cash = self.repo.get_cash_snapshot_at_or_before(latest_pos.timestamp)
        if latest_cash is None:
            return self._empty_summary()

        prior_pos = self.repo.get_prior_position_snapshot(latest_pos.timestamp)
        prior_cash = (
            self.repo.get_cash_snapshot_at_or_before(prior_pos.timestamp)
            if prior_pos is not None
            else None
        )

        first_pos = self.repo.get_first_position_snapshot()
        first_cash = (
            self.repo.get_cash_snapshot_at_or_before(first_pos.timestamp)
            if first_pos is not None
            else None
        )

        current_value = self.repo.compute_total_equity(latest_pos, latest_cash)
        prior_value = (
            self.repo.compute_total_equity(prior_pos, prior_cash)
            if prior_pos is not None and prior_cash is not None
            else current_value
        )
        starting_value = (
            self.repo.compute_total_equity(first_pos, first_cash)
            if first_pos is not None and first_cash is not None
            else current_value
        )

        todays_pnl_amount = current_value - prior_value
        total_pnl_amount = current_value - starting_value

        return {
            "current_portfolio_value": current_value,
            "todays_pnl_amount": todays_pnl_amount,
            "todays_pnl_percent": self._percent_change(todays_pnl_amount, prior_value),
            "total_pnl_amount": total_pnl_amount,
            "total_pnl_percent": self._percent_change(total_pnl_amount, starting_value),
            "cash_balance": Decimal(latest_cash.cash),
        }

    def _percent_change(self, amount: Decimal, base: Decimal) -> Decimal:
        if base == Decimal("0"):
            return Decimal("0")
        return amount / base

    def _empty_summary(self) -> dict:
        return {
            "current_portfolio_value": Decimal("0.00"),
            "todays_pnl_amount": Decimal("0.00"),
            "todays_pnl_percent": Decimal("0.00"),
            "total_pnl_amount": Decimal("0.00"),
            "total_pnl_percent": Decimal("0.00"),
            "cash_balance": Decimal("0.00"),
        }
