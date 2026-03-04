# autonomous_trading_platform/storage/sor/models/fills.py

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import LiquiditySide, Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class Fill(Base):
    __tablename__ = "fills"

    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[Side] = mapped_column(SAEnum(Side, name="side_enum"), nullable=False)
    quantity: Mapped[Quantity] = mapped_column(QuantityType(), nullable=False)

    price: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    fees: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    liquidity: Mapped[LiquiditySide | None] = mapped_column(
        SAEnum(LiquiditySide, name="liquidity_side_enum"), nullable=True
    )
    venue: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
