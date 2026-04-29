from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class SimulationRuns(Base):
    __tablename__ = "simulation_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    experiment_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("experiments.experiment_id"),
        nullable=True,
    )

    strategy_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("strategy_configs.strategy_id"),
        nullable=False,
    )

    dataset_version: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dataset_versions.dataset_version_id"),
        nullable=False,
    )

    universe_version: Mapped[str] = mapped_column(String(64), nullable=False)
    price_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    symbols: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    start_time: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    end_time: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)

    execution_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    metrics_snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
