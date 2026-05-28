from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class CorrelationSnapshotRow(Base):
    """Rolling pairwise correlation matrix snapshot.

    Stores the full correlation matrix (JSONB) plus diagnostic metadata for
    a single (snapshot_type, lookback_window, as_of_date) triple.  One row
    is written per computation run per window per universe type.
    """

    __tablename__ = "correlation_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_type: Mapped[str] = mapped_column(String(32), nullable=False)
    lookback_window: Mapped[int] = mapped_column(Integer, nullable=False)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    as_of_date: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    asset_universe: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observations_used: Mapped[int] = mapped_column(Integer, nullable=False)

    average_pairwise_correlation: Mapped[float] = mapped_column(Float, nullable=False)
    max_pairwise_correlation: Mapped[float] = mapped_column(Float, nullable=False)
    min_pairwise_correlation: Mapped[float] = mapped_column(Float, nullable=False)

    correlation_matrix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    top_correlated_pairs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    clusters: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)

    is_numerically_stable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    condition_number: Mapped[float | None] = mapped_column(Float, nullable=True)

    regime_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    computation_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_corr_snap_type_window_as_of", "snapshot_type", "lookback_window", "as_of_date"),
        Index("ix_corr_snap_computed_at", "computed_at"),
        Index("ix_corr_snap_run_id", "run_id"),
    )


class CovarianceSnapshotRow(Base):
    """Rolling covariance matrix snapshot.

    Stores the annualised covariance matrix plus numerical-stability metadata.
    Downstream optimizers consume these snapshots rather than recomputing
    covariance ad-hoc.
    """

    __tablename__ = "covariance_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_type: Mapped[str] = mapped_column(String(32), nullable=False)
    lookback_window: Mapped[int] = mapped_column(Integer, nullable=False)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    as_of_date: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    asset_universe: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observations_used: Mapped[int] = mapped_column(Integer, nullable=False)
    annualization_factor: Mapped[int] = mapped_column(Integer, nullable=False)

    covariance_matrix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    matrix_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_positive_definite: Mapped[bool] = mapped_column(Boolean, nullable=False)
    condition_number: Mapped[float | None] = mapped_column(Float, nullable=True)

    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    computation_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_cov_snap_type_window_as_of", "snapshot_type", "lookback_window", "as_of_date"),
        Index("ix_cov_snap_computed_at", "computed_at"),
        Index("ix_cov_snap_run_id", "run_id"),
    )
