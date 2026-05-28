from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class FactorExposureSnapshotRow(Base):
    """Top-level factor exposure monitoring snapshot.

    One row is written per portfolio/window/as-of computation. The row stores
    reproducibility metadata plus compact portfolio diagnostics, while strategy
    and portfolio decomposition rows provide query-friendly access for operators.
    """

    __tablename__ = "factor_exposure_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    as_of_date: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    lookback_window: Mapped[int] = mapped_column(Integer, nullable=False)

    benchmark_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    benchmark_source: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_computation_version: Mapped[str] = mapped_column(String(32), nullable=False)

    portfolio_exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    strategy_exposures: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    symbol_exposures: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    sector_exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    concentration_diagnostics: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    factor_methodology: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    data_lineage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index(
            "ix_factor_snap_portfolio_window_as_of", "portfolio_id", "lookback_window", "as_of_date"
        ),
        Index("ix_factor_snap_computed_at", "computed_at"),
        Index("ix_factor_snap_run_id", "run_id"),
        Index("ix_factor_snap_benchmark", "benchmark_symbol"),
    )


class StrategyFactorExposureRow(Base):
    __tablename__ = "strategy_factor_exposures"

    exposure_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    as_of_date: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    lookback_window: Mapped[int] = mapped_column(Integer, nullable=False)
    benchmark_symbol: Mapped[str] = mapped_column(String(32), nullable=False)

    strategy_weight: Mapped[float] = mapped_column(Float, nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False)
    exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    top_symbol_contributors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index(
            "ix_strategy_factor_strategy_window_as_of",
            "strategy_id",
            "lookback_window",
            "as_of_date",
        ),
        Index("ix_strategy_factor_snapshot_id", "snapshot_id"),
        Index("ix_strategy_factor_run_id", "run_id"),
    )


class PortfolioFactorExposureRow(Base):
    __tablename__ = "portfolio_factor_exposures"

    exposure_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    as_of_date: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    lookback_window: Mapped[int] = mapped_column(Integer, nullable=False)
    benchmark_symbol: Mapped[str] = mapped_column(String(32), nullable=False)

    total_weight: Mapped[float] = mapped_column(Float, nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sector_exposures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    concentration_diagnostics: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index(
            "ix_portfolio_factor_portfolio_window_as_of",
            "portfolio_id",
            "lookback_window",
            "as_of_date",
        ),
        Index("ix_portfolio_factor_snapshot_id", "snapshot_id"),
        Index("ix_portfolio_factor_run_id", "run_id"),
    )
