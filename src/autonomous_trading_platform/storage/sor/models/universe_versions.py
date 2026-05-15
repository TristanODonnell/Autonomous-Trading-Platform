# autonomous_trading_platform/storage/sor/models/universe_versions.py

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class UniverseVersion(Base):
    __tablename__ = "universe_versions"

    universe_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    effective_from: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    effective_to: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rebalance_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    config_hash: Mapped[str] = mapped_column(String, nullable=False)

    members: Mapped[list[UniverseMember]] = relationship(
        "UniverseMember",
        back_populates="version",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    __table_args__ = (
        Index("ix_universe_versions_status", "status"),
        Index("ix_universe_versions_effective_from", "effective_from"),
        Index("ix_universe_versions_effective_to", "effective_to"),
    )


class UniverseMember(Base):
    __tablename__ = "universe_members"

    universe_version_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("universe_versions.universe_version_id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    included_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    excluded_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    liquidity_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    quality_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    version: Mapped[UniverseVersion] = relationship(
        "UniverseVersion",
        back_populates="members",
    )

    __table_args__ = (
        Index("ix_universe_members_universe_version_id", "universe_version_id"),
        Index("ix_universe_members_symbol", "symbol"),
    )
