from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class ShadowComparisonSnapshotRow(Base):
    """Persists a point-in-time comparison snapshot for a single category.

    Stores the raw sim vs live values at one bar timestamp for audit,
    replay, and forensic inspection without joining across multiple tables.
    """

    __tablename__ = "shadow_comparison_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shadow_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    comparison_category: Mapped[str] = mapped_column(String(32), nullable=False)

    sim_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    live_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    drift_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_shadow_snap_shadow_run_id", "shadow_run_id"),
        Index("ix_shadow_snap_bar_timestamp", "bar_timestamp"),
        Index("ix_shadow_snap_category", "comparison_category"),
    )
