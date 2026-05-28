from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class RiskBudgetSnapshotRow(Base):
    """Persists the result of one risk budgeting computation run.

    Each row represents a full portfolio risk budget at a point in time:
    which allocation mode was used, what weights were recommended, what the
    realized risk contributions were, and which covariance snapshot was
    consumed.  Downstream optimizers and audit processes query this table
    rather than recomputing on demand.
    """

    __tablename__ = "risk_budget_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)

    strategy_universe: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    strategy_count: Mapped[int] = mapped_column(Integer, nullable=False)

    target_risk_budgets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recommended_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    realized_risk_contributions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    portfolio_volatility_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    diversification_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    covariance_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    covariance_lookback_window: Mapped[int | None] = mapped_column(Integer, nullable=True)

    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    convergence_achieved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    iterations_to_convergence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hidden_concentration_detected: Mapped[bool] = mapped_column(Boolean, nullable=False)

    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_rbs_computed_at", "computed_at"),
        Index("ix_rbs_mode", "mode"),
        Index("ix_rbs_run_id", "run_id"),
        Index("ix_rbs_covariance_snapshot_id", "covariance_snapshot_id"),
    )
