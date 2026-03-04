# autonomous_trading_platform/storage/sor/models/cash_snapshots.py

from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, UTCDateTimeType


class CashSnapshot(Base):
    __tablename__ = "cash_snapshots"

    snapshot_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False, primary_key=True)

    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    cash: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    buying_power: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    reserved_cash: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    equity: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    source: Mapped[OrderSource] = mapped_column(
        SAEnum(OrderSource, name="order_source_enum"), nullable=False
    )
    capital_bucket: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
