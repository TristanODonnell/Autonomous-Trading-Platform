# autonomous_trading_platform/storage/sor/models/bench_evaluations.py

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, Float, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, UTCDateTimeType


class BenchEvaluationRow(Base):
    """
    One strategy's result in one bench review (portfolio rotation step 3).

    Append-only: re-sim metrics on the review window, its correlation group, and the
    decision taken (admit / keep / retire / protected / skipped) with the reason.
    """

    __tablename__ = "bench_evaluations"

    evaluation_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    # Membership status going into the review, or "pending" for unreviewed candidates.
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    resim_run_id: Mapped[UUID | None] = mapped_column(UUID_PK, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)

    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_champion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Highest return correlation with any other reviewed strategy, and which one.
    max_correlation: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlated_with: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Consecutive reviews below bench_score_floor, including this one.
    floor_strikes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_bench_eval_strategy_reviewed", "strategy_id", "reviewed_at"),
        Index("ix_bench_eval_review", "review_id"),
    )
