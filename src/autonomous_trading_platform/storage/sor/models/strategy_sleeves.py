# autonomous_trading_platform/storage/sor/models/strategy_sleeves.py

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class StrategySleevePositionRow(Base):
    """Current holding of one symbol in one strategy's sleeve. Deleted when flat."""

    __tablename__ = "strategy_sleeve_positions"

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    quantity: Mapped[Quantity] = mapped_column(QuantityType(), nullable=False)
    avg_cost: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (Index("ix_ssp_symbol", "symbol"),)


class StrategySleeveLedgerRow(Base):
    """
    Append-only accounting events against a sleeve.

    entry_id is deterministic per (source event, strategy) so replaying the same
    fill or cross is a no-op.
    """

    __tablename__ = "strategy_sleeve_ledger"

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

    __table_args__ = (
        Index("ix_ssl_strategy_timestamp", "strategy_id", "timestamp"),
        Index("ix_ssl_fill_id", "fill_id"),
    )


class StrategySleeveSnapshotRow(Base):
    """Point-in-time valuation of a sleeve (cumulative P&L since inception)."""

    __tablename__ = "strategy_sleeve_snapshots"

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

    __table_args__ = (Index("ix_sss_strategy_timestamp", "strategy_id", "timestamp"),)
