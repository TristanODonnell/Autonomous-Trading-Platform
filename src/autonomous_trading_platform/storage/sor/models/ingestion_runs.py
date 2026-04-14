from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class IngestionRuns(Base):
    __tablename__ = "ingestion_runs"

    ingestion_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    run_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    run_type: Mapped[RunType] = mapped_column(SAEnum(RunType, name="run_type_enum"), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    completed_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
