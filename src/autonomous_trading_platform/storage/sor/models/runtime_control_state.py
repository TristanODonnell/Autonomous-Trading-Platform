from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.storage.sor.models.base import Base


class RuntimeControlState(Base):
    __tablename__ = "runtime_control_state"

    control_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default="global",
    )

    trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trading_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kill_switch_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    trading_mode: Mapped[str] = mapped_column(String, nullable=False, default="paper")

    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
