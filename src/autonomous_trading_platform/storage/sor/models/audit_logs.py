from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, synonym

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    component: Mapped[str] = mapped_column(String, nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(UTCDateTimeType(), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    event_metadata = synonym("metadata_")
