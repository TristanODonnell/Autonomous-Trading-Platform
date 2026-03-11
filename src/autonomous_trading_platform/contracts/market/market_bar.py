# autonomous_trading_platform/contracts/market/market_bar.py

from __future__ import annotations

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    MarketSession,
    PriceBasis,
)
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime


class MarketBar(BaseModel):
    bar_id: str
    timestamp: UTCDateTime
    end_timestamp: UTCDateTime
    interval: BarInterval
    symbol: str
    open: Money
    high: Money
    low: Money
    close: Money
    volume: int
    vwap: Money | None = None
    trade_count: int | None = None
    price_basis: PriceBasis
    adjustment_factor: float
    source: str
    ingested_at: UTCDateTime
    quality_flags: list[str] | None = None
    session: MarketSession
