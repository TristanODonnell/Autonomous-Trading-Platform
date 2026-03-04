# autonomous_trading_platform/storage/sor/models/position_snapshot_items.py

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autonomous_trading_platform.contracts.common.types import Money, Quantity

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType


class PositionSnapshotItem(Base):
    __tablename__ = "position_snapshot_items"

    snapshot_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("position_snapshots.snapshot_id", ondelete="CASCADE"), primary_key=True
    )

    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        primary_key=True,
    )

    quantity: Mapped[Quantity] = mapped_column(
        QuantityType(),
        nullable=False,
    )

    avg_cost: Mapped[Money | None] = mapped_column(
        MoneyType(),
        nullable=True,
    )

    market_price: Mapped[Money | None] = mapped_column(
        MoneyType(),
        nullable=True,
    )

    market_value: Mapped[Money | None] = mapped_column(
        MoneyType(),
        nullable=True,
    )

    unrealized_pnl: Mapped[Money | None] = mapped_column(
        MoneyType(),
        nullable=True,
    )

    # Relationship back to parent
    snapshot = relationship(
        "PositionSnapshot",
        back_populates="positions",
    )

    __table_args__ = (
        # Only one row per symbol per snapshot
        UniqueConstraint(
            "snapshot_id",
            "symbol",
            name="uq_position_snapshot_items_snapshot_symbol",
        ),
    )
