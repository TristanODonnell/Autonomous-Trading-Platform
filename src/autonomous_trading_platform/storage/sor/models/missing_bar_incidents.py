from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class MissingBarIncidents(Base):
    __tablename__ = "missing_bar_incidents"

    incident_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingestion_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    resolved_flag: Mapped[bool] = mapped_column(nullable=False, default=False)
