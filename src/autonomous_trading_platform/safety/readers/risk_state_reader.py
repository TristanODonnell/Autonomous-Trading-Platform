from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill


class StubRiskStateReader:
    def get_gross_exposure(self) -> float:
        return 0.0

    def get_symbol_exposure(self, symbol: str) -> float:
        return 0.0

    def get_daily_notional_traded(self, trade_date: date) -> float:
        return 0.0

    def get_reserved_cash(self) -> float:
        return 0.0

    def get_position_qty(self, symbol: str) -> float:
        return 0.0


class PositionAwareRiskStateReader(StubRiskStateReader):
    """Account-level risk state for the live pre-trade checks.

    Symbol exposure, position quantity and gross exposure come from the latest
    position snapshot (the ledger, which post-fill accounting keeps in step with the
    broker); daily notional traded is the sum of today's booked fills. Reserved cash
    keeps the stub's zero: the live path does not reserve cash at scheduling time.

    Orders submitted earlier in the same tick are not in any snapshot yet, so the
    submission job reports each accepted order through ``record_submitted_order`` and
    the reader adds it to what it answers for the rest of the tick. Without that, every
    intent in a bar was checked against the same opening state and a cap could be
    overshot by one bar's worth of orders.
    """

    def __init__(
        self,
        portfolio_reader: PortfolioRiskStateReader,
        *,
        daily_notional_traded: Callable[[date], float] | None = None,
    ) -> None:
        self._portfolio_reader = portfolio_reader
        self._daily_notional_traded = daily_notional_traded
        self._pending_gross_delta = 0.0
        self._pending_symbol_delta: dict[str, float] = {}
        self._pending_daily_notional = 0.0

    @classmethod
    def from_session(cls, session: Session) -> PositionAwareRiskStateReader:
        return cls(
            PortfolioRiskStateReader.from_session(session),
            daily_notional_traded=lambda trade_date: fills_notional_on(session, trade_date),
        )

    def get_gross_exposure(self) -> float:
        base = float(self._portfolio_reader.get_gross_exposure_usd())
        return max(0.0, base + self._pending_gross_delta)

    def get_symbol_exposure(self, symbol: str) -> float:
        base = float(abs(self._portfolio_reader.get_symbol_exposure_usd(symbol)))
        return max(0.0, base + self._pending_symbol_delta.get(_normalize(symbol), 0.0))

    def get_daily_notional_traded(self, trade_date: date) -> float:
        base = self._daily_notional_traded(trade_date) if self._daily_notional_traded else 0.0
        return base + self._pending_daily_notional

    def get_position_qty(self, symbol: str) -> float:
        return float(self._portfolio_reader.get_symbol_quantity(symbol))

    def record_submitted_order(self, *, symbol: str, side: Side, notional: float) -> None:
        """Count an order the broker accepted this tick, before any snapshot shows it.

        Mirrors the pre-trade exposure arithmetic: a buy adds its notional; a sell first
        closes what is held (reducing exposure) and only the remainder adds.
        """
        key = _normalize(symbol)
        notional = abs(float(notional))
        if side == Side.BUY:
            delta = notional
        else:
            held = self.get_symbol_exposure(symbol)
            reduction = min(notional, held)
            delta = max(notional - reduction, 0.0) - reduction
        self._pending_symbol_delta[key] = self._pending_symbol_delta.get(key, 0.0) + delta
        self._pending_gross_delta += delta
        self._pending_daily_notional += notional


def fills_notional_on(session: Session, trade_date: date) -> float:
    """Sum of |quantity x price| over the fills booked on ``trade_date`` (UTC)."""
    start = datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    stmt = select(func.coalesce(func.sum(func.abs(Fill.quantity) * Fill.price), 0)).where(
        Fill.timestamp >= start,
        Fill.timestamp < end,
    )
    total = session.execute(stmt).scalar_one()
    return float(Decimal(str(total)))


def _normalize(symbol: str) -> str:
    return symbol.strip().upper()
