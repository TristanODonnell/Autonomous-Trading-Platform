from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class BlendedMetricsSnapshot(Base):
    """Persisted blended metric snapshot for a single strategy rebalance cycle.

    Stores the alpha weight, component scores, and references to the source
    research and live metric snapshots so the blending rationale is fully
    auditable after the fact.
    """

    __tablename__ = "blended_metrics_snapshots"

    blended_snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rebalance_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    metric_lineage_type: Mapped[str] = mapped_column(String(32), nullable=False, default="blended")
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calculation_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    alpha_weight: Mapped[float] = mapped_column(Float, nullable=False)
    days_live: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    research_score: Mapped[float] = mapped_column(Float, nullable=False)
    live_score: Mapped[float] = mapped_column(Float, nullable=False)
    blended_score: Mapped[float] = mapped_column(Float, nullable=False)

    research_metrics_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    live_metrics_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_bms_strategy_id_computed_at", "strategy_id", "computed_at"),
        Index("ix_bms_rebalance_run_id", "rebalance_run_id"),
    )
