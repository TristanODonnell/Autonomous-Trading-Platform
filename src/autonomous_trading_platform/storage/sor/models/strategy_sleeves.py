# autonomous_trading_platform/storage/sor/models/strategy_sleeves.py
"""
Strategy sleeve tables.

Two books with identical columns:
  strategy_sleeve_*  real sleeves — broker fills; must sum to the broker account.
  shadow_sleeve_*    on-deck shadow sleeves — simulated fills, no capital. Kept in
                     separate tables so they can never enter the sleeve/account
                     invariant, internal crossing or adoption.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class SleevePositionBase(Base):
    """Current holding of one symbol in one strategy's sleeve. Deleted when flat."""

    __abstract__ = True

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    quantity: Mapped[Quantity] = mapped_column(QuantityType(), nullable=False)
    avg_cost: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)


class SleeveLedgerBase(Base):
    """
    Append-only accounting events against a sleeve.

    entry_id is deterministic per (source event, strategy) so replaying the same
    fill or cross is a no-op.
    """

    __abstract__ = True

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Quantity] = mapped_column(QuantityType(), nullable=False)
    price: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    fees: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    realized_pnl: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    fill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cross_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent_id: Mapped[UUID | None] = mapped_column(UUID_PK, nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(UUID_PK, nullable=True)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)


class SleeveSnapshotBase(Base):
    """Point-in-time valuation of a sleeve (cumulative P&L since inception)."""

    __abstract__ = True

    snapshot_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(UUID_PK, nullable=True)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    allocated_capital: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    market_value: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    cost_basis: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    realized_pnl: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    unrealized_pnl: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    fees: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    net_pnl: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unpriced_symbols: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# Real book
# ---------------------------------------------------------------------------


class StrategySleevePositionRow(SleevePositionBase):
    __tablename__ = "strategy_sleeve_positions"
    __table_args__ = (Index("ix_ssp_symbol", "symbol"),)


class StrategySleeveLedgerRow(SleeveLedgerBase):
    __tablename__ = "strategy_sleeve_ledger"
    __table_args__ = (
        Index("ix_ssl_strategy_timestamp", "strategy_id", "timestamp"),
        Index("ix_ssl_fill_id", "fill_id"),
    )


class StrategySleeveSnapshotRow(SleeveSnapshotBase):
    __tablename__ = "strategy_sleeve_snapshots"
    __table_args__ = (Index("ix_sss_strategy_timestamp", "strategy_id", "timestamp"),)


# ---------------------------------------------------------------------------
# Shadow book (on-deck)
# ---------------------------------------------------------------------------


class ShadowSleevePositionRow(SleevePositionBase):
    __tablename__ = "shadow_sleeve_positions"
    __table_args__ = (Index("ix_shsp_symbol", "symbol"),)


class ShadowSleeveLedgerRow(SleeveLedgerBase):
    __tablename__ = "shadow_sleeve_ledger"
    __table_args__ = (Index("ix_shsl_strategy_timestamp", "strategy_id", "timestamp"),)


class ShadowSleeveSnapshotRow(SleeveSnapshotBase):
    __tablename__ = "shadow_sleeve_snapshots"
    __table_args__ = (Index("ix_shss_strategy_timestamp", "strategy_id", "timestamp"),)

    # Shadow orders dropped by the pre-trade risk check or the order throttle this cycle.
    blocked_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
