from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class RawMarketSymbol(Base):
    __tablename__ = "raw_market_symbols"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_tradable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    marginable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shortable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fractionable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    easy_to_borrow: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    provider_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    last_seen: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_raw_market_symbols_asset_type", "asset_type"),
        Index("ix_raw_market_symbols_status", "status"),
        Index("ix_raw_market_symbols_is_tradable", "is_tradable"),
        Index("ix_raw_market_symbols_exchange", "exchange"),
        Index("ix_raw_market_symbols_last_seen", "last_seen"),
    )


class RawMarketPoolSnapshot(Base):
    __tablename__ = "raw_market_pool_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    captured_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    memberships: Mapped[list[RawMarketPoolMembership]] = relationship(
        "RawMarketPoolMembership",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_raw_market_pool_snapshots_captured_at", "captured_at"),
        Index("ix_raw_market_pool_snapshots_source", "source"),
        Index("ix_raw_market_pool_snapshots_is_complete", "is_complete"),
    )


class RawMarketPoolMembership(Base):
    __tablename__ = "raw_market_pool_memberships"

    snapshot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("raw_market_pool_snapshots.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    is_tradable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)

    snapshot: Mapped[RawMarketPoolSnapshot] = relationship(
        "RawMarketPoolSnapshot",
        back_populates="memberships",
    )

    __table_args__ = (
        Index("ix_raw_market_pool_memberships_snapshot_id", "snapshot_id"),
        Index("ix_raw_market_pool_memberships_symbol", "symbol"),
        Index("ix_raw_market_pool_memberships_asset_type", "asset_type"),
        Index("ix_raw_market_pool_memberships_is_tradable", "is_tradable"),
    )
