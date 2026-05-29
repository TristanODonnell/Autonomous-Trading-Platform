# autonomous_trading_platform/contracts/trading/signal_aggregate.py
from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.signal import Signal


class SignalNettingPolicy(enum.StrEnum):
    # Original policies
    CONSERVATIVE = "conservative"
    DOMINANT = "dominant"
    PROPORTIONAL = "proportional"
    NETTING_ONLY = "netting_only"
    # Rec 6.5 aliases with clearer semantics
    SUPPRESS_CONFLICTS = "suppress_conflicts"  # alias for CONSERVATIVE
    DOMINANT_SIGNAL = "dominant_signal"  # alias for DOMINANT
    NET = "net"  # alias for PROPORTIONAL
    # New weighting policies (Rec 6.5)
    ALLOCATION_WEIGHTED = "allocation_weighted"  # weight by strategy allocation weight
    CONFIDENCE_WEIGHTED = "confidence_weighted"  # weight purely by confidence score


class StrategySignalContribution(BaseModel):
    strategy_id: str
    signal_id: UUID
    direction: SignalDirection
    confidence: float | None
    weight: float  # normalized across all strategies for this symbol


class SignalAggregationConflict(BaseModel):
    symbol: str
    policy_applied: SignalNettingPolicy
    contributions: list[StrategySignalContribution]
    net_direction: SignalDirection
    conflict_detected: bool
    suppressed: bool
    suppressed_notional_usd: float | None = None
    dominant_strategy_id: str | None = None


class AggregatedSignalBundle(BaseModel):
    bar_timestamp: UTCDateTime
    run_id: UUID
    policy: SignalNettingPolicy
    reconciled_signals: list[Signal]
    conflicts: list[SignalAggregationConflict]
    # symbol -> per-strategy contributions (always populated; survives suppression)
    attributions: dict[str, list[StrategySignalContribution]]
    total_strategies: int
    total_symbols_evaluated: int
    total_conflicts_detected: int
    total_suppressed_symbols: int
    aggregation_duration_seconds: float
