"""
SimulationVsPaperComparisonService — compares actual paper fills against
simulated/predicted fills for the same orders (TASK D-02).

Purpose
-------
Enables calibration of the simulation fill model by measuring divergence
between what Alpaca paper trading actually executed and what the simulation
model would have predicted.  Helps answer:

  - Is the simulation model too optimistic on price?
  - Are participation caps too strict or too loose?
  - Are limit touch probabilities realistic?
  - Are TWAP/VWAP slices aligning with paper execution behaviour?
  - Are simulated slippage assumptions close to paper fills?

Architecture
------------
This is a pure computation service — it performs no I/O.  Callers are
responsible for fetching records from the database (FillQualityMetrics,
BrokerOrder, Fill) and from simulation artifacts (SimulationExecutionResult),
then normalising them into PaperFillRecord / SimulatedFillRecord before
calling compare().

Matching keys (in priority order):
  1. intent_id (UUID) — direct 1:1 match
  2. parent_intent_id aggregation — 1:N match for TWAP/VWAP child orders
     produced by D-01 SimulatedExecutionModel.plan(); all sim records whose
     parent_intent_id == str(paper.intent_id) are aggregated into one row.
  3. client_order_id (str) — cross-system fallback

Runtime separation
------------------
This service never calls broker clients or fill simulation services directly.
All simulation predictions are provided as pre-computed SimulatedFillRecord
inputs — no broker or simulation I/O occurs here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.contracts.execution.simulation_vs_paper_comparison import (
    DivergenceFlag,
    PaperFillRecord,
    SimulatedFillRecord,
    SimulationVsPaperComparison,
    SimulationVsPaperComparisonRow,
    SimulationVsPaperComparisonSummary,
)
from autonomous_trading_platform.observability.logging import get_logger

logger = get_logger(__name__)

_BPS = Decimal("10000")
_TWO_DP = Decimal("0.01")
_ZERO = Decimal("0")

# Default divergence thresholds
_DEFAULT_PRICE_THRESHOLD_BPS = Decimal("5")  # flag if |price diff| > 5 bps
_DEFAULT_QTY_THRESHOLD_PCT = Decimal("0.01")  # flag if |qty diff| > 1% of paper qty
_DEFAULT_TS_THRESHOLD_MS = 60_000  # flag if |timestamp diff| > 60 s


class SimulationVsPaperComparisonService:
    """
    Pure computation service for simulation-vs-paper fill comparison.

    Constructor args:
        price_divergence_threshold_bps: Flag PRICE_DIVERGENCE when |price diff| exceeds this.
        qty_divergence_threshold_pct:   Flag QTY_DIVERGENCE when qty diff as a fraction of
                                        paper qty exceeds this (e.g. 0.01 = 1 %).
        timestamp_divergence_threshold_ms: Flag TIMESTAMP_DIVERGENCE when |time diff| > this.
        clock:                          Callable returning the current UTC datetime.
                                        Injected for deterministic testing.
    """

    def __init__(
        self,
        *,
        price_divergence_threshold_bps: Decimal = _DEFAULT_PRICE_THRESHOLD_BPS,
        qty_divergence_threshold_pct: Decimal = _DEFAULT_QTY_THRESHOLD_PCT,
        timestamp_divergence_threshold_ms: int = _DEFAULT_TS_THRESHOLD_MS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._price_threshold_bps = price_divergence_threshold_bps
        self._qty_threshold_pct = qty_divergence_threshold_pct
        self._ts_threshold_ms = timestamp_divergence_threshold_ms
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(
        self,
        paper_fills: list[PaperFillRecord],
        simulated_fills: list[SimulatedFillRecord],
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        policy_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> SimulationVsPaperComparison:
        """
        Compare paper fills against simulated fills and return a structured report.

        Args:
            paper_fills:     Normalised actual fills from the paper broker.
            simulated_fills: Normalised predicted fills from the simulation model.
            strategy_id:     Optional filter label embedded in the report.
            symbol:          Optional filter label embedded in the report.
            policy_type:     Optional filter label embedded in the report.
            start_time:      Optional window start embedded in the report.
            end_time:        Optional window end embedded in the report.

        Returns:
            SimulationVsPaperComparison with deterministic row ordering and
            summary metrics.
        """
        # Build lookup indexes (sort inputs for determinism)
        sorted_paper = sorted(paper_fills, key=lambda r: r.client_order_id)
        sorted_sim = sorted(simulated_fills, key=lambda r: r.client_order_id)

        sim_by_intent: dict[UUID, list[SimulatedFillRecord]] = defaultdict(list)
        sim_by_parent_intent: dict[str, list[SimulatedFillRecord]] = defaultdict(list)
        sim_by_client_id: dict[str, list[SimulatedFillRecord]] = defaultdict(list)

        for rec in sorted_sim:
            if rec.intent_id is not None:
                sim_by_intent[rec.intent_id].append(rec)
            if rec.parent_intent_id is not None:
                sim_by_parent_intent[rec.parent_intent_id].append(rec)
            sim_by_client_id[rec.client_order_id].append(rec)

        # Track which sim records have been matched
        matched_sim_client_ids: set[str] = set()

        rows: list[SimulationVsPaperComparisonRow] = []

        for paper in sorted_paper:
            matched_sims, match_key = self._find_sim_matches(
                paper,
                sim_by_intent=sim_by_intent,
                sim_by_parent_intent=sim_by_parent_intent,
                sim_by_client_id=sim_by_client_id,
            )
            for sim in matched_sims:
                matched_sim_client_ids.add(sim.client_order_id)

            row = self._build_row(paper=paper, matched_sims=matched_sims)
            rows.append(row)

            if not matched_sims:
                logger.warning(
                    "sim_vs_paper.unmatched_paper",
                    extra={
                        "client_order_id": paper.client_order_id,
                        "symbol": paper.symbol,
                        "strategy_id": strategy_id,
                        "intent_id": str(paper.intent_id) if paper.intent_id else None,
                    },
                )

        # Unmatched simulation records
        for sim in sorted_sim:
            if sim.client_order_id not in matched_sim_client_ids:
                row = self._build_row(paper=None, matched_sims=[sim])
                rows.append(row)
                logger.warning(
                    "sim_vs_paper.unmatched_sim",
                    extra={
                        "client_order_id": sim.client_order_id,
                        "symbol": sim.symbol,
                        "strategy_id": strategy_id,
                        "intent_id": str(sim.intent_id) if sim.intent_id else None,
                        "parent_intent_id": sim.parent_intent_id,
                    },
                )

        summary = self._build_summary(
            paper_fills=paper_fills,
            simulated_fills=simulated_fills,
            rows=rows,
        )

        comparison_id = self._generate_comparison_id(
            strategy_id=strategy_id,
            symbol=symbol,
            policy_type=policy_type,
            start_time=start_time,
            end_time=end_time,
        )

        logger.info(
            "sim_vs_paper.report_generated",
            extra={
                "comparison_id": comparison_id,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "policy_type": policy_type,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
                "total_paper": len(paper_fills),
                "total_simulated": len(simulated_fills),
                "matched": summary.matched_orders,
                "divergence_count": summary.divergence_count,
            },
        )

        return SimulationVsPaperComparison(
            comparison_id=comparison_id,
            generated_at=self._clock(),
            start_time=start_time,
            end_time=end_time,
            strategy_id=strategy_id,
            symbol=symbol,
            policy_type=policy_type,
            rows=rows,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _find_sim_matches(
        self,
        paper: PaperFillRecord,
        *,
        sim_by_intent: dict[UUID, list[SimulatedFillRecord]],
        sim_by_parent_intent: dict[str, list[SimulatedFillRecord]],
        sim_by_client_id: dict[str, list[SimulatedFillRecord]],
    ) -> tuple[list[SimulatedFillRecord], str]:
        """
        Return (matched_sim_records, match_key_used).

        Priority:
          1. intent_id exact match (1:1).
          2. parent_intent_id aggregation (1:N for TWAP/VWAP child orders from D-01).
          3. client_order_id fallback (1:1 or 1:N).
        """
        if paper.intent_id is not None:
            by_intent = sim_by_intent.get(paper.intent_id)
            if by_intent:
                return sorted(
                    by_intent, key=lambda r: (r.slice_index or 0, r.client_order_id)
                ), "intent_id"

            # Check for TWAP/VWAP child slices where parent_intent_id = str(paper.intent_id)
            parent_key = str(paper.intent_id)
            children = sim_by_parent_intent.get(parent_key)
            if children:
                return (
                    sorted(children, key=lambda r: (r.slice_index or 0, r.client_order_id)),
                    "parent_intent_id",
                )

        by_client = sim_by_client_id.get(paper.client_order_id)
        if by_client:
            return sorted(
                by_client, key=lambda r: (r.slice_index or 0, r.client_order_id)
            ), "client_order_id"

        return [], "none"

    # ------------------------------------------------------------------
    # Row construction
    # ------------------------------------------------------------------

    def _build_row(
        self,
        *,
        paper: PaperFillRecord | None,
        matched_sims: list[SimulatedFillRecord],
    ) -> SimulationVsPaperComparisonRow:
        """Build a comparison row from one paper record and zero-or-more sim records."""
        if paper is None and not matched_sims:
            raise ValueError("At least one of paper or matched_sims must be provided")

        sim = self._aggregate_sims(matched_sims) if matched_sims else None

        # Derive canonical fields
        client_order_id = paper.client_order_id if paper else matched_sims[0].client_order_id
        broker_order_id = paper.broker_order_id if paper else None
        symbol = paper.symbol if paper else matched_sims[0].symbol
        side = paper.side if paper else matched_sims[0].side
        execution_policy = (
            paper.execution_policy
            if paper and paper.execution_policy
            else (matched_sims[0].execution_policy if matched_sims else None)
        )
        parent_intent_id = (
            paper.parent_intent_id
            if paper and paper.parent_intent_id
            else (matched_sims[0].parent_intent_id if matched_sims else None)
        )

        paper_qty = paper.filled_qty if paper else _ZERO
        sim_qty = sim.filled_qty if sim else _ZERO
        qty_diff = sim_qty - paper_qty

        paper_price = paper.avg_fill_price if paper else None
        sim_price = sim.fill_price if sim else None
        price_diff, price_diff_bps = self._compute_price_diff(paper_price, sim_price)

        paper_ts = paper.fill_timestamp if paper else None
        sim_ts = sim.fill_timestamp if sim else None
        latency_diff_ms = self._compute_latency_diff_ms(paper_ts, sim_ts)

        paper_slip = paper.slippage_bps if paper else None
        sim_slip = sim.slippage_bps if sim else None
        slip_diff = (
            (sim_slip - paper_slip).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
            if sim_slip is not None and paper_slip is not None
            else None
        )

        flags = self._compute_flags(
            paper=paper,
            sim=sim,
            price_diff_bps=price_diff_bps,
            qty_diff=qty_diff,
            paper_qty=paper_qty,
            latency_diff_ms=latency_diff_ms,
            matched_slice_count=len(matched_sims),
        )

        return SimulationVsPaperComparisonRow(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            symbol=symbol,
            side=side,
            execution_policy=execution_policy,
            paper_qty=paper_qty,
            simulated_qty=sim_qty,
            qty_diff=qty_diff,
            paper_price=paper_price,
            simulated_price=sim_price,
            price_diff=price_diff,
            price_diff_bps=price_diff_bps,
            paper_timestamp=paper_ts,
            simulated_timestamp=sim_ts,
            latency_diff_ms=latency_diff_ms,
            paper_slippage_bps=paper_slip,
            simulated_slippage_bps=sim_slip,
            slippage_diff_bps=slip_diff,
            divergence_flags=sorted(flags),  # lexicographic for stability
            parent_intent_id=parent_intent_id,
            matched_slice_count=len(matched_sims),
        )

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def _aggregate_sims(
        self,
        sims: list[SimulatedFillRecord],
    ) -> SimulatedFillRecord:
        """Aggregate multiple sim records (e.g. TWAP slices) into one synthetic record.

        Quantities are summed; fill_price is the quantity-weighted average.
        The first record's metadata is used for scalar fields.
        """
        if len(sims) == 1:
            return sims[0]

        total_qty = sum((r.filled_qty for r in sims), _ZERO)

        # Weighted-average fill price
        weighted_price: Decimal | None = None
        if all(r.fill_price is not None for r in sims) and total_qty > _ZERO:
            weighted_price = sum((r.fill_price or _ZERO) * r.filled_qty for r in sims) / total_qty

        # Weighted-average slippage bps
        weighted_slip: Decimal | None = None
        if all(r.slippage_bps is not None for r in sims) and total_qty > _ZERO:
            weighted_slip = sum((r.slippage_bps or _ZERO) * r.filled_qty for r in sims) / total_qty

        # Earliest fill timestamp
        timestamps = [r.fill_timestamp for r in sims if r.fill_timestamp is not None]
        ts = min(timestamps) if timestamps else None

        first = sims[0]
        return SimulatedFillRecord(
            client_order_id=first.client_order_id,
            symbol=first.symbol,
            side=first.side,
            filled_qty=total_qty,
            requested_qty=sum((r.requested_qty or _ZERO) for r in sims) or None,
            fill_price=weighted_price,
            fill_timestamp=ts,
            reference_price=first.reference_price,
            slippage_bps=(
                weighted_slip.quantize(_TWO_DP, rounding=ROUND_HALF_UP)
                if weighted_slip is not None
                else None
            ),
            execution_policy=first.execution_policy,
            intent_id=first.intent_id,
            parent_intent_id=first.parent_intent_id,
            slice_index=None,  # aggregated — no single slice index
        )

    # ------------------------------------------------------------------
    # Metric computation
    # ------------------------------------------------------------------

    def _compute_price_diff(
        self,
        paper_price: Decimal | None,
        sim_price: Decimal | None,
    ) -> tuple[Decimal | None, Decimal | None]:
        if paper_price is None or sim_price is None:
            return None, None
        diff = (sim_price - paper_price).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
        if paper_price == _ZERO:
            return diff, None
        bps = (diff / paper_price * _BPS).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
        return diff, bps

    @staticmethod
    def _compute_latency_diff_ms(
        paper_ts: datetime | None,
        sim_ts: datetime | None,
    ) -> int | None:
        if paper_ts is None or sim_ts is None:
            return None
        # Normalise to UTC-aware before subtraction.
        if paper_ts.tzinfo is None:
            paper_ts = paper_ts.replace(tzinfo=UTC)
        if sim_ts.tzinfo is None:
            sim_ts = sim_ts.replace(tzinfo=UTC)
        delta_ms = int((sim_ts - paper_ts).total_seconds() * 1000)
        return delta_ms

    # ------------------------------------------------------------------
    # Divergence flag computation
    # ------------------------------------------------------------------

    def _compute_flags(
        self,
        *,
        paper: PaperFillRecord | None,
        sim: SimulatedFillRecord | None,
        price_diff_bps: Decimal | None,
        qty_diff: Decimal,
        paper_qty: Decimal,
        latency_diff_ms: int | None,
        matched_slice_count: int,
    ) -> list[str]:
        flags: list[str] = []

        if paper is None:
            flags.append(DivergenceFlag.MISSING_PAPER_MATCH)
            return flags

        if sim is None:
            flags.append(DivergenceFlag.MISSING_SIMULATION_MATCH)
            return flags

        # Price divergence
        if price_diff_bps is not None and abs(price_diff_bps) > self._price_threshold_bps:
            flags.append(DivergenceFlag.PRICE_DIVERGENCE)

        # Quantity divergence
        base = paper_qty if paper_qty > _ZERO else Decimal("1")
        qty_diff_pct = abs(qty_diff) / base
        if qty_diff_pct > self._qty_threshold_pct:
            flags.append(DivergenceFlag.QTY_DIVERGENCE)

        # Fill status divergence
        paper_filled = paper.filled_qty > _ZERO or paper.avg_fill_price is not None
        sim_filled = sim.filled_qty > _ZERO or sim.fill_price is not None

        if paper_filled and not sim_filled:
            flags.append(DivergenceFlag.PAPER_FILLED_SIM_NOT_FILLED)
        elif sim_filled and not paper_filled:
            flags.append(DivergenceFlag.SIM_FILLED_PAPER_NOT_FILLED)

        # Partial fill divergence: one is partial and the other is complete
        paper_partial = self._is_partial(paper.filled_qty, paper.requested_qty)
        sim_partial = self._is_partial(sim.filled_qty, sim.requested_qty)
        if paper_partial != sim_partial:
            flags.append(DivergenceFlag.PARTIAL_FILL_DIVERGENCE)

        # Timestamp divergence
        if latency_diff_ms is not None and abs(latency_diff_ms) > self._ts_threshold_ms:
            flags.append(DivergenceFlag.TIMESTAMP_DIVERGENCE)

        # Policy slice divergence: paper has slice metadata, sim slice count disagrees
        # Also fires when TWAP/VWAP slices were expected but not all matched.
        if matched_slice_count > 1:
            # Multiple sim slices aggregated: check total qty matches
            if qty_diff_pct > self._qty_threshold_pct:
                flags.append(DivergenceFlag.POLICY_SLICE_DIVERGENCE)
        elif (
            paper.slice_index is not None
            and sim.slice_index is not None
            and paper.slice_index != sim.slice_index
        ):
            flags.append(DivergenceFlag.POLICY_SLICE_DIVERGENCE)

        return flags

    @staticmethod
    def _is_partial(filled_qty: Decimal, requested_qty: Decimal | None) -> bool:
        if requested_qty is None or requested_qty <= _ZERO:
            return False
        return filled_qty < requested_qty * Decimal("0.99")  # <99% = partial

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        *,
        paper_fills: list[PaperFillRecord],
        simulated_fills: list[SimulatedFillRecord],
        rows: list[SimulationVsPaperComparisonRow],
    ) -> SimulationVsPaperComparisonSummary:
        unmatched_paper = sum(
            1 for r in rows if DivergenceFlag.MISSING_SIMULATION_MATCH in r.divergence_flags
        )
        unmatched_sim = sum(
            1 for r in rows if DivergenceFlag.MISSING_PAPER_MATCH in r.divergence_flags
        )
        matched = len(paper_fills) - unmatched_paper

        # Averages over matched rows only
        price_diffs = [r.price_diff_bps for r in rows if r.price_diff_bps is not None]
        avg_price_diff = (
            (sum(price_diffs, _ZERO) / Decimal(str(len(price_diffs)))).quantize(
                _TWO_DP, rounding=ROUND_HALF_UP
            )
            if price_diffs
            else None
        )

        slip_diffs = [r.slippage_diff_bps for r in rows if r.slippage_diff_bps is not None]
        avg_slip_diff = (
            (sum(slip_diffs, _ZERO) / Decimal(str(len(slip_diffs)))).quantize(
                _TWO_DP, rounding=ROUND_HALF_UP
            )
            if slip_diffs
            else None
        )

        lat_diffs = [r.latency_diff_ms for r in rows if r.latency_diff_ms is not None]
        avg_lat = int(sum(lat_diffs) / len(lat_diffs)) if lat_diffs else None

        fill_rate_paper = self._compute_fill_rate(
            [(p.filled_qty, p.requested_qty) for p in paper_fills]
        )
        fill_rate_sim = self._compute_fill_rate(
            [(s.filled_qty, s.requested_qty) for s in simulated_fills]
        )

        divergence_count = sum(
            1
            for r in rows
            if any(
                f
                not in (
                    DivergenceFlag.MISSING_PAPER_MATCH,
                    DivergenceFlag.MISSING_SIMULATION_MATCH,
                )
                for f in r.divergence_flags
            )
            or r.divergence_flags
        )

        return SimulationVsPaperComparisonSummary(
            total_paper_orders=len(paper_fills),
            total_simulated_orders=len(simulated_fills),
            matched_orders=matched,
            unmatched_paper_orders=unmatched_paper,
            unmatched_simulated_orders=unmatched_sim,
            avg_price_diff_bps=avg_price_diff,
            avg_slippage_diff_bps=avg_slip_diff,
            avg_latency_diff_ms=avg_lat,
            fill_rate_paper=fill_rate_paper,
            fill_rate_simulated=fill_rate_sim,
            divergence_count=divergence_count,
        )

    @staticmethod
    def _compute_fill_rate(
        pairs: list[tuple[Decimal, Decimal | None]],
    ) -> Decimal | None:
        valid = [(filled, req) for filled, req in pairs if req is not None and req > _ZERO]
        if not valid:
            return None
        rates = [filled / req for filled, req in valid]
        avg = sum(rates, _ZERO) / Decimal(str(len(rates)))
        return avg.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    # ------------------------------------------------------------------
    # Comparison ID
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_comparison_id(
        *,
        strategy_id: str | None,
        symbol: str | None,
        policy_type: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> str:
        key = "|".join(
            [
                strategy_id or "",
                symbol or "",
                policy_type or "",
                start_time.isoformat() if start_time else "",
                end_time.isoformat() if end_time else "",
            ]
        )
        return str(uuid5(NAMESPACE_URL, f"sim-vs-paper:{key}"))
