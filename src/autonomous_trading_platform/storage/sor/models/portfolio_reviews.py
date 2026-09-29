# autonomous_trading_platform/storage/sor/models/portfolio_reviews.py

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, Float, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, UTCDateTimeType


class PortfolioReviewRow(Base):
    """One portfolio review run (portfolio rotation step 4). Append-only."""

    __tablename__ = "portfolio_reviews"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reviewed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    # off / advisory / auto at the time of the review.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # Monthly review: active swaps allowed.
    swap_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Bench review whose re-sims this review reused.
    bench_review_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scorecard_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_portfolio_reviews_reviewed_at", "reviewed_at"),)


class PortfolioScorecardRow(Base):
    """One strategy's scorecard in one review: evidence, weights, lens penalties, rank."""

    __tablename__ = "portfolio_scorecards"

    scorecard_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    forward_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    forward_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    forward_weight: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, default=0)
    forward_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forward_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resim_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    resim_weight: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, default=0)
    backtest_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    backtest_weight: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, default=0)
    backtest_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)

    decay_penalty: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    health_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    health_penalty: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    mean_correlation: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlation_penalty: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    blocked_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    blocked_penalty: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    regime_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_portfolio_scorecards_review", "review_id"),
        Index("ix_portfolio_scorecards_strategy_reviewed", "strategy_id", "reviewed_at"),
    )


class PortfolioReviewDecisionRow(Base):
    """One decision of one review (swap, seat, on-deck exchange, challenge, re-weight)."""

    __tablename__ = "portfolio_review_decisions"

    decision_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    counterpart_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    counterpart_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    guardrails: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        Index("ix_portfolio_review_decisions_review", "review_id"),
        Index("ix_portfolio_review_decisions_strategy_reviewed", "strategy_id", "reviewed_at"),
    )
