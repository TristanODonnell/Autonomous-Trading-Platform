# autonomous_trading_platform/storage/sor/models/capital_allocation_policies.py

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class CapitalAllocationPolicies(Base):
    __tablename__ = "capital_allocation_policies"

    __table_args__ = (
        UniqueConstraint(
            "approval_status",
            "performance_tier",
            name="uq_cap_policy_status_tier",
        ),
        Index("ix_cap_policy_status_active", "approval_status", "is_active"),
    )

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Values: "approved_research" | "approved_paper" | "approved_live"
    approval_status: Mapped[str] = mapped_column(String(64), nullable=False)
    performance_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_pct_of_capital: Mapped[float] = mapped_column(Float, nullable=False)
    # e.g. 0.05 = strategy can receive at most 5% of total portfolio capital
    max_position_size_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_allowed: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
