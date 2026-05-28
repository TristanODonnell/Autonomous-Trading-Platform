from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class ShadowDivergenceRow(Base):
    """Persists one detected divergence event within a shadow validation run.

    Each row captures the specific metric, category, and magnitude of a
    divergence between simulated and live/paper behavior, along with whether
    the configured threshold was exceeded.
    """

    __tablename__ = "shadow_divergences"

    divergence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shadow_run_id: Mapped[str] = mapped_column(String(64), nullable=False)

    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    divergence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)

    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    sim_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    live_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence_magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    exceeds_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False)

    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_shadow_div_shadow_run_id", "shadow_run_id"),
        Index("ix_shadow_div_divergence_type", "divergence_type"),
        Index("ix_shadow_div_category", "category"),
        Index("ix_shadow_div_exceeds_threshold", "exceeds_threshold"),
        Index("ix_shadow_div_timestamp", "timestamp"),
        Index("ix_shadow_div_symbol", "symbol"),
    )
