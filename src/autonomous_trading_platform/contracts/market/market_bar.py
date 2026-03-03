# autonomous_trading_platform/contracts/market/market_bar.py
from datetime import datetime

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
)


class MarketBar(BaseModel):
    bar_id: str
    timestamp: datetime
    end_timestamp: datetime
    interval: BarInterval
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None
    trade_count: int | None = None
    price_basis: PriceBasis
    adjustment_factor: float
    source: str
    ingested_at: datetime
    quality_flags: list[str] | None = None
