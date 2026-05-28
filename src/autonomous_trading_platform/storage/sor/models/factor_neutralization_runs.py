from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class FactorNeutralizationRunRow(Base):
    """Persists one factor-neutralization evaluation or adjustment run."""

    __tablename__ = "factor_neutralization_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    factor_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    covariance_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    optimization_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    original_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pre_exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    post_exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    exposure_reduction: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    residual_exposure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    constraint_utilization: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    binding_constraints: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    constraint_violations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    fallback_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    infeasibility_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_factor_neut_generated_at", "generated_at"),
        Index("ix_factor_neut_status", "status"),
        Index("ix_factor_neut_mode", "mode"),
        Index("ix_factor_neut_portfolio_id", "portfolio_id"),
        Index("ix_factor_neut_factor_snapshot_id", "factor_snapshot_id"),
        Index("ix_factor_neut_optimization_run_id", "optimization_run_id"),
    )
