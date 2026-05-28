from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class ShadowRunRow(Base):
    """Persists one shadow validation run linking a simulation to a live/paper execution.

    Acts as the root aggregate for shadow mode comparisons.  All divergence
    records and comparison snapshots reference this row via shadow_run_id.
    """

    __tablename__ = "shadow_runs"

    shadow_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    simulation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    live_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    shadow_mode_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)

    total_divergences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threshold_exceedances: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    divergence_by_category: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    promotion_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    required_passing_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    covariance_snapshot_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    factor_snapshot_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allocation_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    optimizer_run_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    started_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    completed_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_shadow_runs_strategy_id", "strategy_id"),
        Index("ix_shadow_runs_shadow_mode_type", "shadow_mode_type"),
        Index("ix_shadow_runs_status", "status"),
        Index("ix_shadow_runs_validation_status", "validation_status"),
        Index("ix_shadow_runs_started_at", "started_at"),
        Index("ix_shadow_runs_simulation_run_id", "simulation_run_id"),
        Index("ix_shadow_runs_live_run_id", "live_run_id"),
    )
