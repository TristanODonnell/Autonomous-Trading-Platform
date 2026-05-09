# storage/sor/models/operator_settings.py

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.storage.sor.models.base import Base


class OperatorSettingsRow(Base):
    __tablename__ = "operator_settings"

    settings_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    risk_tolerance: Mapped[str] = mapped_column(String(16), nullable=False)
    max_drawdown_limit: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    rebalance_frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    auto_promote_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    per_strategy_cap: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)

    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
