# autonomous_trading_platform/storage/sor/models/broker_account_snapshots.py

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, UTCDateTimeType


class BrokerAccountSnapshot(Base):
    __tablename__ = "broker_account_snapshots"

    snapshot_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)

    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    broker_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    cash: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    buying_power: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    equity: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    portfolio_value: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)

    observed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
