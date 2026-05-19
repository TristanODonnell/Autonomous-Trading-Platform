from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class UniverseRebalanceRun(Base):
    __tablename__ = "universe_rebalance_runs"

    rebalance_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    previous_universe_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_universe_version_id: Mapped[str] = mapped_column(String, nullable=False)
    proposed_universe_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    added_symbols_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    removed_symbols_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    retained_symbols_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    churn_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_universe_size: Mapped[int] = mapped_column(Integer, nullable=False)
    max_churn_pct: Mapped[float] = mapped_column(Float, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_rebalance_runs_candidate_version_id", "candidate_universe_version_id"),
        Index("ix_rebalance_runs_previous_version_id", "previous_universe_version_id"),
        Index("ix_rebalance_runs_proposed_version_id", "proposed_universe_version_id"),
        Index("ix_rebalance_runs_created_at", "created_at"),
    )
