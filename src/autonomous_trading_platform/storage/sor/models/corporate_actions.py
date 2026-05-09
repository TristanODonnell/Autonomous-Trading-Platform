# autonomous_trading_platform/storage/sor/models/corporate_actions.py

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, Float, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime

from .base import Base
from .helpers.sa_types import MoneyType, UTCDateTimeType


class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)

    action_type: Mapped[CorporateActionType] = mapped_column(
        SAEnum(CorporateActionType, name="corporate_action_type_enum"), nullable=False
    )

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    announced_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payable_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    split_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)

    currency: Mapped[str] = mapped_column(String(64), nullable=False)

    new_symbol: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped[str] = mapped_column(String(64), nullable=False)

    ingested_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "action_type",
            "effective_date",
            "source",
            name="uq_corporate_actions_symbol_type_date_source",
        ),
    )
