# autonomous_trading_platform/storage/sor/models/universe_snapshots.py
# DEPRECATED: UniverseSnapshot has been superseded by UniverseVersion + UniverseMember.
# See: storage/sor/models/universe_versions.py
# This file and the universe_snapshots table are retained for historical data only.
# Do not use UniverseSnapshot in new code.

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import ARRAY, Date, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class UniverseSnapshot(Base):
    __tablename__ = "universe_snapshots"

    universe_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_start: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    effective_end: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    symbols: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    built_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
