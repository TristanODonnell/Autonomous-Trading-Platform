# autonomous_trading_platform/contracts/trading/portfolio_signal.py
"""
Portfolio construction layer contracts (Recommendation 6.5).

Defines the intermediate artifacts produced by the two-phase portfolio
construction pipeline:

  Signal Phase:   strategies → SignalBatch (raw signals)
  Portfolio Phase: aggregate → PortfolioSignal (netted) →
                  apply constraints → SignalIntent (constrained) →
                  generate → OrderIntent (executable)
"""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.signal_aggregate import (
    AggregatedSignalBundle,
    SignalAggregationConflict,
    SignalNettingPolicy,
    StrategySignalContribution,
)


class ConstraintApplicationStatus(enum.StrEnum):
    PENDING = "pending"
    APPLIED = "applied"  # constraint evaluated; signal passed
    REJECTED = "rejected"  # constraint rejected the signal (suppressed)


class PortfolioSignal(BaseModel):
    """
    Netted portfolio-level signal produced after cross-strategy aggregation.

    Represents the portfolio's consolidated view for one symbol — after
    conflicting strategy signals have been reconciled but before portfolio
    constraints are applied.  All contributing strategy signals are preserved
    in ``attribution`` for governance and PnL attribution.
    """

    portfolio_signal_id: UUID
    batch_id: UUID
    run_id: UUID
    bar_timestamp: UTCDateTime
    symbol: str
    net_direction: SignalDirection
    net_confidence: float
    gross_buy_notional: float | None = None
    gross_sell_notional: float | None = None
    net_notional: float | None = None
    contributing_strategy_ids: list[str]
    conflict_detected: bool
    attribution: list[StrategySignalContribution]
    netting_policy: SignalNettingPolicy
    constraint_status: ConstraintApplicationStatus = ConstraintApplicationStatus.PENDING


class SignalBatch(BaseModel):
    """
    Raw collection of strategy signals for one evaluation cycle.

    Created at the start of Phase 1 before any aggregation or filtering.
    Acts as the provenance anchor for all downstream artifacts in the cycle.
    """

    batch_id: UUID
    run_id: UUID
    bar_timestamp: UTCDateTime
    collected_at: UTCDateTime
    strategy_count: int
    symbol_count: int
    raw_signal_count: int
    strategy_ids: list[str]


class SignalIntent(BaseModel):
    """
    Portfolio-level declared intent after constraints are applied.

    Produced by Phase 3 (constraint application).  Only APPLIED intents
    proceed to Phase 4 (order generation).  REJECTED intents are logged
    for governance and replay debugging.
    """

    intent_id: UUID
    portfolio_signal_id: UUID
    batch_id: UUID
    run_id: UUID
    bar_timestamp: UTCDateTime
    symbol: str
    direction: SignalDirection
    confidence: float
    constraint_status: ConstraintApplicationStatus
    constraints_triggered: list[str]
    attribution: list[StrategySignalContribution]


class PortfolioConstructionDiagnostics(BaseModel):
    """
    Gross/net reconciliation report for one complete pipeline run.

    Exposes the exposure transformation at each phase so operators and
    governance reviewers can trace exactly how raw strategy demand was
    shaped into final executable order intents.
    """

    batch_id: UUID
    run_id: UUID
    bar_timestamp: UTCDateTime
    constructed_at: UTCDateTime
    netting_policy: str
    strategy_count: int
    symbol_count: int
    raw_signal_count: int
    netted_signal_count: int
    suppressed_count: int
    suppressed_notional_usd: float
    conflict_count: int
    constraint_applied_count: int
    constraint_rejected_count: int
    raw_gross_signal_exposure: float
    net_signal_exposure: float
    exposure_before_constraints: float
    exposure_after_constraints: float
    final_order_count: int
    construction_duration_seconds: float


class PortfolioConstructionResult(BaseModel):
    """Full output of one portfolio construction pipeline run."""

    signal_batch: SignalBatch
    aggregated_bundle: AggregatedSignalBundle
    portfolio_signals: list[PortfolioSignal]
    signal_intents: list[SignalIntent]
    conflicts: list[SignalAggregationConflict]
    diagnostics: PortfolioConstructionDiagnostics
