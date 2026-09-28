# autonomous_trading_platform/storage/sor/models/portfolio_memberships.py

from __future__ import annotations

from sqlalchemy import Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class PortfolioMembershipRow(Base):
    """Current portfolio membership status of a strategy (one row per strategy)."""

    __tablename__ = "portfolio_memberships"

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    since: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (Index("ix_pm_status", "status"),)


class PortfolioMembershipTransitionRow(Base):
    """Append-only audit trail of membership status changes."""

    __tablename__ = "portfolio_membership_transitions"

    transition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(128), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (Index("ix_pmt_strategy_created", "strategy_id", "created_at"),)
