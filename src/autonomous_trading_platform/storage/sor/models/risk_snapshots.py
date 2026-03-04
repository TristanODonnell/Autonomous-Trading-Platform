# autonomous_trading_platform/storage/sor/models/risk_snapshots.py

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Float, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, UTCDateTimeType


class RiskSnapshot(Base):
    __tablename__ = "risk_snapshots"

    snapshot_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    gross_exposure: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    net_exposure: Mapped[Money] = mapped_column(MoneyType(), nullable=False)

    leverage: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    utilization: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    block_reasons: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)), nullable=True)
