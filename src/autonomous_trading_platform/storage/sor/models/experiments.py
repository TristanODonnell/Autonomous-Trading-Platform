from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class Experiments(Base):
    __tablename__ = "experiments"

    experiment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    strategy_set_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    parameter_grid_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    universe_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_time: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    end_time: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
