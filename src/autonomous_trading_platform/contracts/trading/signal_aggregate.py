# autonomous_trading_platform/contracts/trading/signal_aggregate.py
from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.signal import Signal


class SignalNettingPolicy(enum.StrEnum):
    CONSERVATIVE = "conservative"
    DOMINANT = "dominant"
    PROPORTIONAL = "proportional"
    NETTING_ONLY = "netting_only"


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
