# autonomous_trading_platform/storage/sor/models/promotion_rules.py

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class PromotionRules(Base):
    __tablename__ = "promotion_rules"

    __table_args__ = (
        Index("ix_promotion_rules_transition", "from_status", "to_status", "is_active"),
    )

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # State transition this rule governs
    # from_status e.g. "approved_research" | "approved_paper"
    # to_status   e.g. "approved_paper"    | "approved_live"
    from_status: Mapped[str] = mapped_column(String(64), nullable=False)
    to_status: Mapped[str] = mapped_column(String(64), nullable=False)
    min_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    # e.g. 0.15 = drawdown must be below 15% over the test window
    min_days_tested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Optional minimum compound annual growth rate
    min_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
