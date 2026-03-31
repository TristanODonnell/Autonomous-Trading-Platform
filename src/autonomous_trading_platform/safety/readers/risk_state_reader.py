from __future__ import annotations

from datetime import date


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
