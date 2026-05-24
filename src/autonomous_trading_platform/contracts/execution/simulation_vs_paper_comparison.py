"""
Contracts for the simulation-vs-paper fill comparison report (TASK D-02).

These types define the inputs and outputs of SimulationVsPaperComparisonService.
They carry no business logic — all computation lives in the service.

Input types:
  PaperFillRecord      — normalised view of one actual paper/live broker fill.
  SimulatedFillRecord  — normalised view of one simulated/predicted fill.

Both are constructed by callers from whatever source is available:
  PaperFillRecord  ← FillQualityMetrics + BrokerOrder + Fill (from RealisedSlippageService)
  SimulatedFillRecord ← SimulationExecutionResult.trade_logs or FillQualityMetrics.expected_*

Output types:
  SimulationVsPaperComparisonRow     — one matched (or unmatched) order comparison.
  SimulationVsPaperComparisonSummary — aggregate metrics across all rows.
  SimulationVsPaperComparison        — complete report.

Matching keys (in priority order):
  1. intent_id (UUID) — most stable; used for 1:1 PASSTHROUGH/MARKET/LIMIT matching.
  2. parent_intent_id (str UUID) + slice aggregation — for TWAP/VWAP-lite child orders
     produced by D-01 SimulatedExecutionModel.plan(); sim records where
     parent_intent_id == str(paper.intent_id) are aggregated into one comparison row.
  3. client_order_id (str) — fallback for cross-system matching.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class DivergenceFlag(enum.StrEnum):
    """Deterministic divergence classification flags.

    Each flag is independent — a single row may carry multiple flags.
    Flags are sorted lexicographically in output lists for stable ordering.
    """

    PRICE_DIVERGENCE = "PRICE_DIVERGENCE"
    QTY_DIVERGENCE = "QTY_DIVERGENCE"
    TIMESTAMP_DIVERGENCE = "TIMESTAMP_DIVERGENCE"
    PAPER_FILLED_SIM_NOT_FILLED = "PAPER_FILLED_SIM_NOT_FILLED"
    SIM_FILLED_PAPER_NOT_FILLED = "SIM_FILLED_PAPER_NOT_FILLED"
    PARTIAL_FILL_DIVERGENCE = "PARTIAL_FILL_DIVERGENCE"
    POLICY_SLICE_DIVERGENCE = "POLICY_SLICE_DIVERGENCE"
    MISSING_SIMULATION_MATCH = "MISSING_SIMULATION_MATCH"
    MISSING_PAPER_MATCH = "MISSING_PAPER_MATCH"


@dataclass(frozen=True)
class PaperFillRecord:
    """Normalised view of one actual paper/live broker fill.

    Construct from FillQualityMetrics + BrokerOrder:
        PaperFillRecord(
            client_order_id = broker_order.client_order_id,
            intent_id       = UUID(fqm.intent_id),
            symbol          = fqm.symbol,
            side            = fqm.side.value,
            filled_qty      = Decimal(str(broker_order.filled_qty)),
            requested_qty   = Decimal(str(broker_order.qty)) if broker_order.qty else None,
            avg_fill_price  = Decimal(str(broker_order.avg_fill_price)) if broker_order.avg_fill_price else None,
            fill_timestamp  = broker_order.first_fill_at,
            reference_price = Decimal(str(fqm.reference_price)) if fqm.reference_price else None,
            slippage_bps    = Decimal(str(fqm.slippage_bps)) if fqm.slippage_bps else None,
            execution_policy= fqm.policy_mode,
            broker_order_id = broker_order.broker_order_id,
        )
    """

    client_order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    filled_qty: Decimal

    requested_qty: Decimal | None = None
    avg_fill_price: Decimal | None = None
    fill_timestamp: datetime | None = None
    reference_price: Decimal | None = None
    slippage_bps: Decimal | None = None
    execution_policy: str | None = None
    broker_order_id: str | None = None
    intent_id: UUID | None = None
    # D-01 child-order traceability: set when this is a TWAP/VWAP slice fill.
    parent_intent_id: str | None = None
    slice_index: int | None = None


@dataclass(frozen=True)
class SimulatedFillRecord:
    """Normalised view of one simulated/predicted fill.

    For PASSTHROUGH/MARKET/LIMIT (1:1 with paper):
        Construct from FillQualityMetrics.expected_fill_price or from
        SimulationExecutionResult.trade_logs (a row for a matching intent).

    For TWAP/VWAP-lite (N sim records → 1 paper record):
        Set parent_intent_id = str(parent.intent_id) and slice_index = i.
        The service aggregates all slices that share the same parent_intent_id
        into a single comparison row against the paper fill for that parent.
    """

    client_order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    filled_qty: Decimal

    requested_qty: Decimal | None = None
    fill_price: Decimal | None = None
    fill_timestamp: datetime | None = None
    reference_price: Decimal | None = None
    slippage_bps: Decimal | None = None
    execution_policy: str | None = None
    intent_id: UUID | None = None
    # D-01 child-order traceability: set for TWAP/VWAP child intents.
    parent_intent_id: str | None = None
    slice_index: int | None = None


@dataclass
class SimulationVsPaperComparisonRow:
    """One matched (or unmatched) paper-vs-simulation comparison record.

    A row is created for every paper fill and for every unmatched simulation fill.
    When a paper order was sliced into TWAP/VWAP children, the simulation side is
    the aggregate of all matched child slices (sum of qty, weighted-average price).
    """

    # Order identity
    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    execution_policy: str | None

    # Quantities
    paper_qty: Decimal
    simulated_qty: Decimal
    qty_diff: Decimal  # simulated_qty - paper_qty

    # Prices
    paper_price: Decimal | None
    simulated_price: Decimal | None
    price_diff: Decimal | None  # simulated_price - paper_price
    price_diff_bps: Decimal | None  # price_diff / paper_price * 10000

    # Timestamps
    paper_timestamp: datetime | None
    simulated_timestamp: datetime | None
    latency_diff_ms: int | None  # (sim_ts - paper_ts) in ms; negative = sim earlier

    # Slippage
    paper_slippage_bps: Decimal | None
    simulated_slippage_bps: Decimal | None
    slippage_diff_bps: Decimal | None  # simulated - paper; negative = sim tighter

    # Divergence classification (sorted lexicographically for stability)
    divergence_flags: list[str]

    # TWAP/VWAP traceability (D-01)
    parent_intent_id: str | None = None
    matched_slice_count: int = 0  # >1 means N sim slices were aggregated


@dataclass
class SimulationVsPaperComparisonSummary:
    """Aggregate metrics across all rows in a comparison report."""

    total_paper_orders: int
    total_simulated_orders: int
    matched_orders: int
    unmatched_paper_orders: int
    unmatched_simulated_orders: int

    # Average divergence metrics (None if no matched rows with data)
    avg_price_diff_bps: Decimal | None
    avg_slippage_diff_bps: Decimal | None
    avg_latency_diff_ms: int | None

    # Fill rates: filled_qty / requested_qty, averaged across orders that have both
    fill_rate_paper: Decimal | None
    fill_rate_simulated: Decimal | None

    # How many rows carry at least one divergence flag
    divergence_count: int


@dataclass
class SimulationVsPaperComparison:
    """Complete simulation-vs-paper fill comparison report.

    comparison_id is deterministic: same strategy_id, symbol, policy_type,
    start_time, end_time always yield the same ID for a given report boundary.

    generated_at is the only wall-clock field (injected externally).
    rows are ordered: paper-matched rows first (sorted by client_order_id),
    then unmatched-simulation rows (sorted by client_order_id).
    """

    comparison_id: str
    generated_at: datetime
    start_time: datetime | None
    end_time: datetime | None
    strategy_id: str | None
    symbol: str | None
    policy_type: str | None
    rows: list[SimulationVsPaperComparisonRow]
    summary: SimulationVsPaperComparisonSummary
