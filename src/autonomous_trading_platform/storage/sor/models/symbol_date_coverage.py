from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class SymbolDateCoverages(Base):
    __tablename__ = "symbol_date_coverages"
    coverage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completeness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    gap_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
