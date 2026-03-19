from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class TrackedOrder(Base):
    __tablename__ = "tracked_orders"

    order_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    intent_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)

    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)

    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    current_status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="tracked_order_status_enum"),
        nullable=False,
    )

    previous_filled_qty: Mapped[Quantity] = mapped_column(
        QuantityType(),
        nullable=False,
        default=Decimal("0"),
    )
    previous_avg_fill_price: Mapped[Money | None] = mapped_column(
        MoneyType(),
        nullable=True,
    )

    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
