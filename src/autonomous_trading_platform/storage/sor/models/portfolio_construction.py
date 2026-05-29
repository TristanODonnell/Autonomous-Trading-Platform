# autonomous_trading_platform/storage/sor/models/portfolio_construction.py
"""
SOR models for the Portfolio Construction Pipeline — Recommendation 6.5.

Four tables store the artifacts produced at each pipeline phase to support
replay debugging, governance review, strategy validation, and shadow-mode
comparisons:

  portfolio_construction_runs       — one row per complete pipeline run
  portfolio_signal_batch_items      — raw strategy signals (Phase 1)
  portfolio_netted_signals          — aggregated portfolio signals (Phase 2)
  portfolio_signal_intents          — constraint-gated intents (Phase 3)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class PortfolioConstructionRunRow(Base):
    """
    One row per complete portfolio construction pipeline run.

    Stores aggregate diagnostics (Phase 1–4 summary statistics) and the
    full diagnostics payload as JSONB for UI and governance consumption.
    """

    __tablename__ = "portfolio_construction_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    constructed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    netting_policy: Mapped[str] = mapped_column(String(32), nullable=False)

    strategy_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    raw_signal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    netted_signal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suppressed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suppressed_notional_usd: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    constraint_applied_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    constraint_rejected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    raw_gross_signal_exposure: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    net_signal_exposure: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    exposure_before_constraints: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    exposure_after_constraints: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    final_order_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    construction_duration_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )

    diagnostics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_pc_run_run_id", "run_id"),
        Index("ix_pc_run_bar_timestamp", "bar_timestamp"),
        Index("ix_pc_run_constructed_at", "constructed_at"),
    )


class PortfolioSignalBatchItemRow(Base):
    """
    One row per raw strategy signal collected in Phase 1.

    Preserves every signal as received from each strategy before any
    aggregation or filtering.  Indexed by batch_id so the full raw
    signal collection for any construction run can be fetched efficiently.
    """

    __tablename__ = "portfolio_signal_batch_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    strategy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_snapshot_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_pc_batch_item_batch_id", "batch_id"),
        Index("ix_pc_batch_item_run_id", "run_id"),
        Index("ix_pc_batch_item_strategy_id", "strategy_id"),
    )


class PortfolioNettedSignalRow(Base):
    """
    One row per aggregated PortfolioSignal produced in Phase 2.

    Captures the result of cross-strategy signal netting for each symbol,
    including conflict detection status and full strategy attribution.
    """

    __tablename__ = "portfolio_netted_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_signal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    net_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    net_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    conflict_detected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    constraint_status: Mapped[str] = mapped_column(String(16), nullable=False)
    netting_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    gross_buy_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_sell_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    contributing_strategy_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    attribution_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_pc_netted_batch_id", "batch_id"),
        Index("ix_pc_netted_run_id", "run_id"),
        Index("ix_pc_netted_symbol", "symbol"),
    )


class PortfolioSignalIntentRow(Base):
    """
    One row per SignalIntent produced in Phase 3 (constraint application).

    Records whether each portfolio signal passed or was rejected by
    portfolio constraints, and which constraints were triggered.
    """

    __tablename__ = "portfolio_signal_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    portfolio_signal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    constraint_status: Mapped[str] = mapped_column(String(16), nullable=False)
    constraints_triggered: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    attribution_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_pc_intent_batch_id", "batch_id"),
        Index("ix_pc_intent_run_id", "run_id"),
        Index("ix_pc_intent_symbol", "symbol"),
        Index("ix_pc_intent_status", "constraint_status"),
    )
