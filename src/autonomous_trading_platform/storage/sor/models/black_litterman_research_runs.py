from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class BlackLittermanResearchRunRow(Base):
    """Persists one research-only Black-Litterman allocation artifact."""

    __tablename__ = "black_litterman_research_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    universe_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    universe: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    universe_size: Mapped[int] = mapped_column(Integer, nullable=False)

    covariance_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    covariance_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tau: Mapped[float] = mapped_column(Float, nullable=False)
    risk_aversion: Mapped[float] = mapped_column(Float, nullable=False)

    views: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    confidences: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    prior_returns: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    posterior_returns: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    posterior_covariance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    proposed_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    constraints_used: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    views_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    optimizer_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_blr_generated_at", "generated_at"),
        Index("ix_blr_covariance_snapshot_id", "covariance_snapshot_id"),
        Index("ix_blr_input_hash", "input_hash"),
        Index("ix_blr_artifact_hash", "artifact_hash"),
    )
