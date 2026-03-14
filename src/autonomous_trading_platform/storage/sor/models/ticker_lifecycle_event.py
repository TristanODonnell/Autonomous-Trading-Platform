from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.storage.sor.models import Base


class TickerLifecycleEventType(StrEnum):
    RENAME = "rename"
    DELISTING = "delisting"
    MERGER = "merger"
    SUCCESSOR = "successor"


class TickerLifecycleEvent(Base):
    __tablename__ = "ticker_lifecycle_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)

    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    new_symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    successor_symbol: Mapped[str | None] = mapped_column(String, nullable=True)

    source: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
