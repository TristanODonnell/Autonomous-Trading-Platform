from __future__ import annotations

from uuid import UUID

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class StrategyGovernance(Base):
    __tablename__ = "strategy_governance"

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_state: Mapped[str] = mapped_column(String(32), nullable=False)
    experiment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    submitted_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
