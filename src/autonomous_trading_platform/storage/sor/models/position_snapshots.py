# autonomous_trading_platform/storage/sor/models/position_snapshots.py

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, UTCDateTimeType


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    snapshot_id: Mapped[UUID] = mapped_column(
        UUID_PK,
        primary_key=True,
    )

    run_id: Mapped[UUID] = mapped_column(
        UUID_PK,
        nullable=False,
    )

    timestamp: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(),
        nullable=False,
    )

    source: Mapped[OrderSource] = mapped_column(
        SAEnum(OrderSource, name="order_source_enum"),
        nullable=False,
    )

    # Relationship to child rows
    positions = relationship(
        "PositionSnapshotItem",
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Prevent duplicate snapshots for same run + timestamp + source
        UniqueConstraint(
            "run_id",
            "timestamp",
            "source",
            name="uq_position_snapshots_run_ts_source",
        ),
    )
