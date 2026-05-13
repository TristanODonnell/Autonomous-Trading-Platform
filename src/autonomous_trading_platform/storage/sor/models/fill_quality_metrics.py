# autonomous_trading_platform/storage/sor/models/fill_quality_metrics.py
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Float, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, synonym

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class FillQualityMetrics(Base):
    __tablename__ = "fill_quality_metrics"

    record_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    intent_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    fill_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    signal_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    submission_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    fill_timestamp: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    submission_latency_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    fill_latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    expected_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    slippage_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    slippage_notional: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    slippage_bps: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    fill_vs_expected_bps: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    commission_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    spread_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    slippage_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    policy_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    is_adverse_fill: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    policy_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    policy_metadata_json = synonym("policy_metadata")
