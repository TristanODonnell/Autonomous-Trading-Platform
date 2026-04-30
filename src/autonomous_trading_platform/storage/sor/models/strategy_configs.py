from __future__ import annotations

from typing import Any

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class StrategyConfigs(Base):
    __tablename__ = "strategy_configs"

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    strategy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (UniqueConstraint("config_hash", name="uq_strategy_configs_config_hash"),)
