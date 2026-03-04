# autonomous_trading_platform/storage/sor/models/market_bars.py

from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime

from .base import Base
from .helpers.sa_types import MoneyType, UTCDateTimeType


class MarketBar(Base):
    __tablename__ = "market_bars"

    bar_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    end_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    interval: Mapped[BarInterval] = mapped_column(
        SAEnum(BarInterval, name="bar_interval_enum"),
        nullable=False,
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)

    open: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    high: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    low: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    close: Mapped[Money] = mapped_column(MoneyType(), nullable=False)

    volume: Mapped[int] = mapped_column(Integer, nullable=False)

    vwap: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    price_basis: Mapped[PriceBasis] = mapped_column(
        SAEnum(PriceBasis, name="price_basis_enum"),
        nullable=False,
    )

    adjustment_factor: Mapped[float] = mapped_column(Float, nullable=False)

    source: Mapped[str] = mapped_column(String(64), nullable=False)

    ingested_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    quality_flags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "interval",
            "timestamp",
            "price_basis",
            name="uq_market_bars_symbol_interval_ts_price_basis",
        ),
    )
