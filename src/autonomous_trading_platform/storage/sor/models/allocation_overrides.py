# autonomous_trading_platform/storage/sor/models/allocation_overrides.py

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class AllocationOverrides(Base):
    __tablename__ = "allocation_overrides"

    __table_args__ = (Index("ix_alloc_override_strategy_active", "strategy_id", "is_active"),)
    override_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    overridden_by: Mapped[str] = mapped_column(String(128), nullable=False)
    override_reason: Mapped[str] = mapped_column(String(512), nullable=False)
    max_pct_of_capital: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_position_size_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_allowed: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    expires_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
