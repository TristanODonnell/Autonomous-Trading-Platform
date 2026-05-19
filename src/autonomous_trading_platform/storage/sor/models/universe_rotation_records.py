from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class UniverseRotationRecord(Base):
    __tablename__ = "universe_rotation_records"

    rotation_id: Mapped[str] = mapped_column(String, primary_key=True)
    rotation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_universe_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_universe_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    new_universe_version_id: Mapped[str] = mapped_column(String, nullable=False)
    rebalance_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    added_symbols_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    removed_symbols_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    retained_symbols_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    churn_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    force_rotation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rotation_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    rollback_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_rotation_records_new_version_id", "new_universe_version_id"),
        Index("ix_rotation_records_previous_version_id", "previous_universe_version_id"),
        Index("ix_rotation_records_rotation_type", "rotation_type"),
        Index("ix_rotation_records_created_at", "created_at"),
        Index("ix_rotation_records_status", "status"),
    )
