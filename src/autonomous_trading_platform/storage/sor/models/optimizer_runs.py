from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class OptimizerRunRow(Base):
    """Persists one mean-variance optimizer run with full input/output metadata.

    Designed for auditability and shadow-mode comparison: every run records
    the covariance snapshot consumed, the expected-return source, all applied
    constraints, the solver status, and the resulting target weights.
    """

    __tablename__ = "optimizer_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    objective: Mapped[str] = mapped_column(String(64), nullable=False)
    solver_status: Mapped[str] = mapped_column(String(32), nullable=False)
    fallback_mode: Mapped[str] = mapped_column(String(32), nullable=False)

    generated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)

    asset_universe: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False)

    target_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    current_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    expected_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    objective_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_aversion: Mapped[float] = mapped_column(Float, nullable=False)

    covariance_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_return_source: Mapped[str] = mapped_column(String(128), nullable=False)

    constraints_applied: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    binding_constraints: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    infeasibility_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    convergence_achieved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    iterations_to_convergence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_opt_generated_at", "generated_at"),
        Index("ix_opt_solver_status", "solver_status"),
        Index("ix_opt_dry_run", "dry_run"),
        Index("ix_opt_covariance_snapshot_id", "covariance_snapshot_id"),
    )
