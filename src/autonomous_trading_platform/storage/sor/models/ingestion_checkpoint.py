from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import CheckpointScope, CheckpointStatus
from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class IngestionCheckpoint(Base):
    __tablename__ = "ingestion_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingestion_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ingestion_runs.ingestion_run_id"),
        nullable=False,
    )

    dataset_version: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dataset_versions.dataset_version_id"),
        nullable=False,
    )

    checkpoint_scope: Mapped[CheckpointScope] = mapped_column(
        SAEnum(CheckpointScope, name="checkpoint_scope_enum"), nullable=False
    )
    checkpoint_status: Mapped[CheckpointStatus] = mapped_column(
        SAEnum(CheckpointStatus, name="checkpoint_status_enum"), nullable=False
    )

    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    checkpoint_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_timestamp: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)

    last_successful_bar_timestamp: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(),
        nullable=True,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
