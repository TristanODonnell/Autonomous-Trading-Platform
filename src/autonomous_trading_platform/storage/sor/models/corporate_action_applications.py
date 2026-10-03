# autonomous_trading_platform/storage/sor/models/corporate_action_applications.py
"""
Which corporate action has been applied to which book.

One row per (action_id, book, scope). The trading cycle and the backtest consult it
before touching a position, so a restart, a replayed tick or a second scheduler never
applies a split or dividend twice.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Date, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class CorporateActionApplicationRow(Base):
    __tablename__ = "corporate_action_applications"

    application_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # account | sleeve | shadow_sleeve
    book: Mapped[str] = mapped_column(String(32), nullable=False)
    # strategy_id for sleeves, "__account__" for the account book
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[CorporateActionType] = mapped_column(
        SAEnum(CorporateActionType, name="corporate_action_type_enum"), nullable=False
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    applied_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    quantity_before: Mapped[Quantity] = mapped_column(QuantityType(), nullable=False)
    quantity_after: Mapped[Quantity] = mapped_column(QuantityType(), nullable=False)
    avg_cost_before: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    avg_cost_after: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    cash_delta: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    realized_pnl: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(UUID_PK, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("action_id", "book", "scope", name="uq_caa_action_book_scope"),
        Index("ix_caa_symbol_effective", "symbol", "effective_date"),
    )
