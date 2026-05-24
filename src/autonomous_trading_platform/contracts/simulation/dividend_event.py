from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class DividendEvent(BaseModel):
    """Cash dividend event for simulation total-return accounting.

    Positions held at the open of ex_date receive cash_amount_per_share
    multiplied by shares held, added to settled cash immediately (no
    settlement delay).  Only the first bar whose date matches ex_date fires
    the event — intraday bars on the same date are skipped.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    ex_date: date
    cash_amount_per_share: Decimal
    payable_date: date | None = None
    declaration_date: date | None = None
    source_dataset_version: str | None = None
    currency: str = "USD"

    @field_validator("symbol")
    @classmethod
    def _symbol_non_empty(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be empty")
        return v

    @field_validator("cash_amount_per_share")
    @classmethod
    def _positive_amount(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("cash_amount_per_share must be > 0")
        return v
