from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)


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
    """Symbol-level risk state from the latest position snapshot.

    Symbol exposure and position quantity come from real holdings, so a sell that
    reduces a held position is recognised as risk-reducing and the per-symbol cap
    applies to what the account actually holds. With the stub, every order looked
    like brand-new exposure: an appreciated position above the symbol cap could
    never be sold.

    Gross exposure, daily notional and reserved cash intentionally keep the stub's
    zero (per-order) semantics: the configured limits for those are absolute
    dollar amounts sized for a small paper account and must be re-based on equity
    before they are enforced against real aggregate state.
    """

    def __init__(self, portfolio_reader: PortfolioRiskStateReader) -> None:
        self._portfolio_reader = portfolio_reader

    @classmethod
    def from_session(cls, session: Session) -> PositionAwareRiskStateReader:
        return cls(PortfolioRiskStateReader.from_session(session))

    def get_symbol_exposure(self, symbol: str) -> float:
        return float(abs(self._portfolio_reader.get_symbol_exposure_usd(symbol)))

    def get_position_qty(self, symbol: str) -> float:
        return float(self._portfolio_reader.get_symbol_quantity(symbol))
