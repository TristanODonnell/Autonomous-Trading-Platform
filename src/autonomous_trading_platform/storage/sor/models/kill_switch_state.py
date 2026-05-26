from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.storage.sor.models.base import Base

KILL_SWITCH_SINGLETON_ID = "current"


class KillSwitchState(Base):
    __tablename__ = "kill_switch_state"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=KILL_SWITCH_SINGLETON_ID,
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set when the kill switch is enabled.
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when the kill switch is cleared.
    cleared_by: Mapped[str | None] = mapped_column(String, nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
