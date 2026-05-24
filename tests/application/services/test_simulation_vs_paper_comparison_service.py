"""
Tests for SimulationVsPaperComparisonService (TASK D-02).

Coverage:
  - Exact match: zero divergence
  - Price divergence: PRICE_DIVERGENCE flag + bps computed correctly
  - Qty divergence: QTY_DIVERGENCE flag
  - Timestamp divergence: TIMESTAMP_DIVERGENCE flag + latency_diff_ms
  - Fill status: PAPER_FILLED_SIM_NOT_FILLED / SIM_FILLED_PAPER_NOT_FILLED
  - Partial fill: PARTIAL_FILL_DIVERGENCE flag
  - Unmatched paper: MISSING_SIMULATION_MATCH flag
  - Unmatched simulation: MISSING_PAPER_MATCH flag
  - TWAP/VWAP policy matching: D-01 parent_intent_id aggregation
  - Policy slice divergence: POLICY_SLICE_DIVERGENCE flag
  - Summary correctness: totals, fill rates, averages
  - Deterministic output: stable row ordering, IDs, flags
  - Empty inputs: no paper, no sim, or both empty
  - Runtime separation: service imports no broker code
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from autonomous_trading_platform.application.services.simulation_vs_paper_comparison_service import (
    SimulationVsPaperComparisonService,
)
from autonomous_trading_platform.contracts.execution.simulation_vs_paper_comparison import (
    DivergenceFlag,
    PaperFillRecord,
    SimulatedFillRecord,
    SimulationVsPaperComparison,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
_START = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
_END = datetime(2025, 6, 1, 16, 0, 0, tzinfo=UTC)
_INTENT_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_INTENT_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")
_INTENT_C = UUID("cccccccc-0000-0000-0000-000000000003")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _paper(
    client_order_id: str = "ord-1",
    symbol: str = "AAPL",
    side: str = "buy",
    filled_qty: float = 1000.0,
    avg_fill_price: float | None = 100.00,
    fill_timestamp: datetime | None = _NOW,
    reference_price: float | None = 100.00,
    slippage_bps: float | None = 5.0,
    execution_policy: str | None = "passthrough",
    broker_order_id: str | None = "bro-1",
    intent_id: UUID | None = _INTENT_A,
    requested_qty: float | None = None,
    parent_intent_id: str | None = None,
    slice_index: int | None = None,
) -> PaperFillRecord:
    return PaperFillRecord(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        filled_qty=Decimal(str(filled_qty)),
        requested_qty=Decimal(str(requested_qty)) if requested_qty is not None else None,
        avg_fill_price=Decimal(str(avg_fill_price)) if avg_fill_price is not None else None,
        fill_timestamp=fill_timestamp,
        reference_price=Decimal(str(reference_price)) if reference_price is not None else None,
        slippage_bps=Decimal(str(slippage_bps)) if slippage_bps is not None else None,
        execution_policy=execution_policy,
        broker_order_id=broker_order_id,
        intent_id=intent_id,
        parent_intent_id=parent_intent_id,
        slice_index=slice_index,
    )


def _sim(
    client_order_id: str = "ord-1",
    symbol: str = "AAPL",
    side: str = "buy",
    filled_qty: float = 1000.0,
    fill_price: float | None = 100.00,
    fill_timestamp: datetime | None = _NOW,
    reference_price: float | None = 100.00,
    slippage_bps: float | None = 5.0,
    execution_policy: str | None = "passthrough",
    intent_id: UUID | None = _INTENT_A,
    requested_qty: float | None = None,
    parent_intent_id: str | None = None,
    slice_index: int | None = None,
) -> SimulatedFillRecord:
    return SimulatedFillRecord(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        filled_qty=Decimal(str(filled_qty)),
        requested_qty=Decimal(str(requested_qty)) if requested_qty is not None else None,
        fill_price=Decimal(str(fill_price)) if fill_price is not None else None,
        fill_timestamp=fill_timestamp,
        reference_price=Decimal(str(reference_price)) if reference_price is not None else None,
        slippage_bps=Decimal(str(slippage_bps)) if slippage_bps is not None else None,
        execution_policy=execution_policy,
        intent_id=intent_id,
        parent_intent_id=parent_intent_id,
        slice_index=slice_index,
    )


def _svc(**kwargs) -> SimulationVsPaperComparisonService:
    return SimulationVsPaperComparisonService(
        clock=lambda: _NOW,
        **kwargs,
    )


def _compare(
    papers: list[PaperFillRecord],
    sims: list[SimulatedFillRecord],
    **kwargs,
) -> SimulationVsPaperComparison:
    return _svc().compare(papers, sims, **kwargs)


# ── Exact match ────────────────────────────────────────────────────────────────


class TestExactMatch:
    def test_exact_match_no_divergence_flags(self) -> None:
        result = _compare([_paper()], [_sim()])
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.divergence_flags == []

    def test_exact_match_qty_diff_is_zero(self) -> None:
        result = _compare([_paper(filled_qty=500.0)], [_sim(filled_qty=500.0)])
        assert result.rows[0].qty_diff == Decimal("0")

    def test_exact_match_price_diff_is_zero(self) -> None:
        result = _compare([_paper(avg_fill_price=150.0)], [_sim(fill_price=150.0)])
        assert result.rows[0].price_diff == Decimal("0.00")
        assert result.rows[0].price_diff_bps == Decimal("0.00")

    def test_exact_match_summary_zero_divergence(self) -> None:
        result = _compare([_paper()], [_sim()])
        assert result.summary.matched_orders == 1
        assert result.summary.unmatched_paper_orders == 0
        assert result.summary.unmatched_simulated_orders == 0
        assert result.summary.divergence_count == 0

    def test_exact_match_preserves_metadata(self) -> None:
        result = _compare([_paper(broker_order_id="bro-99")], [_sim()])
        assert result.rows[0].broker_order_id == "bro-99"
        assert result.rows[0].symbol == "AAPL"
        assert result.rows[0].side == "buy"


# ── Price divergence ───────────────────────────────────────────────────────────


class TestPriceDivergence:
    def test_price_divergence_flag_added_when_above_threshold(self) -> None:
        # Paper=$100, Sim=$101 → diff=100 bps > default 5 bps
        result = _compare([_paper(avg_fill_price=100.0)], [_sim(fill_price=101.0)])
        assert DivergenceFlag.PRICE_DIVERGENCE in result.rows[0].divergence_flags

    def test_price_diff_bps_computed_correctly(self) -> None:
        result = _compare([_paper(avg_fill_price=100.0)], [_sim(fill_price=101.0)])
        # diff = 1.0, paper = 100.0, bps = 1/100*10000 = 100.00
        assert result.rows[0].price_diff_bps == Decimal("100.00")

    def test_price_diff_absolute_computed_correctly(self) -> None:
        result = _compare([_paper(avg_fill_price=200.0)], [_sim(fill_price=200.5)])
        assert result.rows[0].price_diff == Decimal("0.50")

    def test_no_price_divergence_flag_below_threshold(self) -> None:
        # Default threshold=5 bps. Paper=$100, Sim=$100.001 → 0.01 bps → no flag
        result = _compare([_paper(avg_fill_price=100.0)], [_sim(fill_price=100.001)])
        assert DivergenceFlag.PRICE_DIVERGENCE not in result.rows[0].divergence_flags

    def test_price_divergence_threshold_configurable(self) -> None:
        svc = SimulationVsPaperComparisonService(
            price_divergence_threshold_bps=Decimal("1"),
            clock=lambda: _NOW,
        )
        # 1.5 bps diff — below default (5), above custom (1)
        result = svc.compare(
            [_paper(avg_fill_price=1000.0)],
            [_sim(fill_price=1000.15)],  # 0.15/1000*10000 = 1.5 bps
        )
        assert DivergenceFlag.PRICE_DIVERGENCE in result.rows[0].divergence_flags

    def test_price_diff_none_when_paper_price_missing(self) -> None:
        result = _compare([_paper(avg_fill_price=None)], [_sim(fill_price=100.0)])
        assert result.rows[0].price_diff is None
        assert result.rows[0].price_diff_bps is None


# ── Quantity divergence ────────────────────────────────────────────────────────


class TestQtyDivergence:
    def test_qty_divergence_flag_added(self) -> None:
        result = _compare([_paper(filled_qty=1000.0)], [_sim(filled_qty=800.0)])
        assert DivergenceFlag.QTY_DIVERGENCE in result.rows[0].divergence_flags

    def test_qty_diff_is_sim_minus_paper(self) -> None:
        result = _compare([_paper(filled_qty=1000.0)], [_sim(filled_qty=900.0)])
        assert result.rows[0].qty_diff == Decimal("-100")

    def test_no_qty_divergence_below_threshold(self) -> None:
        # Default threshold = 1%. 1000 vs 1005 → 0.5%
        result = _compare([_paper(filled_qty=1000.0)], [_sim(filled_qty=1005.0)])
        assert DivergenceFlag.QTY_DIVERGENCE not in result.rows[0].divergence_flags

    def test_qty_divergence_threshold_configurable(self) -> None:
        svc = SimulationVsPaperComparisonService(
            qty_divergence_threshold_pct=Decimal("0.001"),
            clock=lambda: _NOW,
        )
        # 5 shares on 1000 = 0.5% > 0.1% custom threshold
        result = svc.compare([_paper(filled_qty=1000.0)], [_sim(filled_qty=1005.0)])
        assert DivergenceFlag.QTY_DIVERGENCE in result.rows[0].divergence_flags


# ── Fill status divergence ─────────────────────────────────────────────────────


class TestFillStatusDivergence:
    def test_paper_filled_sim_not_filled(self) -> None:
        result = _compare(
            [_paper(filled_qty=1000.0, avg_fill_price=100.0)],
            [_sim(filled_qty=0.0, fill_price=None)],
        )
        assert DivergenceFlag.PAPER_FILLED_SIM_NOT_FILLED in result.rows[0].divergence_flags

    def test_sim_filled_paper_not_filled(self) -> None:
        result = _compare(
            [_paper(filled_qty=0.0, avg_fill_price=None)],
            [_sim(filled_qty=1000.0, fill_price=100.0)],
        )
        assert DivergenceFlag.SIM_FILLED_PAPER_NOT_FILLED in result.rows[0].divergence_flags

    def test_both_zero_qty_no_fill_status_flag(self) -> None:
        result = _compare(
            [_paper(filled_qty=0.0, avg_fill_price=None)],
            [_sim(filled_qty=0.0, fill_price=None)],
        )
        assert DivergenceFlag.PAPER_FILLED_SIM_NOT_FILLED not in result.rows[0].divergence_flags
        assert DivergenceFlag.SIM_FILLED_PAPER_NOT_FILLED not in result.rows[0].divergence_flags


# ── Partial fill divergence ────────────────────────────────────────────────────


class TestPartialFillDivergence:
    def test_paper_partial_sim_complete_flags_divergence(self) -> None:
        result = _compare(
            [_paper(filled_qty=500.0, requested_qty=1000.0)],
            [_sim(filled_qty=1000.0, requested_qty=1000.0)],
        )
        assert DivergenceFlag.PARTIAL_FILL_DIVERGENCE in result.rows[0].divergence_flags

    def test_sim_partial_paper_complete_flags_divergence(self) -> None:
        result = _compare(
            [_paper(filled_qty=1000.0, requested_qty=1000.0)],
            [_sim(filled_qty=500.0, requested_qty=1000.0)],
        )
        assert DivergenceFlag.PARTIAL_FILL_DIVERGENCE in result.rows[0].divergence_flags

    def test_both_partial_no_divergence_flag(self) -> None:
        result = _compare(
            [_paper(filled_qty=500.0, requested_qty=1000.0)],
            [_sim(filled_qty=480.0, requested_qty=1000.0)],
        )
        assert DivergenceFlag.PARTIAL_FILL_DIVERGENCE not in result.rows[0].divergence_flags

    def test_no_partial_flag_when_requested_qty_missing(self) -> None:
        result = _compare(
            [_paper(filled_qty=500.0, requested_qty=None)],
            [_sim(filled_qty=1000.0, requested_qty=None)],
        )
        assert DivergenceFlag.PARTIAL_FILL_DIVERGENCE not in result.rows[0].divergence_flags


# ── Timestamp divergence ───────────────────────────────────────────────────────


class TestTimestampDivergence:
    def test_timestamp_divergence_flag_when_above_threshold(self) -> None:
        paper_ts = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        sim_ts = datetime(2025, 6, 1, 10, 5, 0, tzinfo=UTC)  # 5 min = 300_000 ms > 60_000 ms
        result = _compare(
            [_paper(fill_timestamp=paper_ts)],
            [_sim(fill_timestamp=sim_ts)],
        )
        assert DivergenceFlag.TIMESTAMP_DIVERGENCE in result.rows[0].divergence_flags

    def test_latency_diff_ms_computed_correctly(self) -> None:
        paper_ts = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        sim_ts = datetime(2025, 6, 1, 10, 0, 1, tzinfo=UTC)  # 1 second later
        result = _compare(
            [_paper(fill_timestamp=paper_ts)],
            [_sim(fill_timestamp=sim_ts)],
        )
        assert result.rows[0].latency_diff_ms == 1000

    def test_negative_latency_when_sim_earlier_than_paper(self) -> None:
        paper_ts = datetime(2025, 6, 1, 10, 0, 1, tzinfo=UTC)
        sim_ts = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)  # 1 second earlier
        result = _compare(
            [_paper(fill_timestamp=paper_ts)],
            [_sim(fill_timestamp=sim_ts)],
        )
        assert result.rows[0].latency_diff_ms == -1000

    def test_no_timestamp_divergence_within_threshold(self) -> None:
        paper_ts = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        sim_ts = datetime(2025, 6, 1, 10, 0, 30, tzinfo=UTC)  # 30 s < 60 s threshold
        result = _compare(
            [_paper(fill_timestamp=paper_ts)],
            [_sim(fill_timestamp=sim_ts)],
        )
        assert DivergenceFlag.TIMESTAMP_DIVERGENCE not in result.rows[0].divergence_flags

    def test_latency_diff_none_when_timestamps_missing(self) -> None:
        result = _compare(
            [_paper(fill_timestamp=None)],
            [_sim(fill_timestamp=None)],
        )
        assert result.rows[0].latency_diff_ms is None


# ── Unmatched records ──────────────────────────────────────────────────────────


class TestUnmatchedRecords:
    def test_unmatched_paper_gets_missing_simulation_flag(self) -> None:
        paper = _paper(
            client_order_id="ord-99", intent_id=UUID("99999999-0000-0000-0000-000000000009")
        )
        result = _compare([paper], [_sim()])  # sim has different client_order_id
        # ord-99 has no sim match; ord-1 is a sim match with no paper
        unmatched_paper_rows = [
            r for r in result.rows if DivergenceFlag.MISSING_SIMULATION_MATCH in r.divergence_flags
        ]
        assert any(r.client_order_id == "ord-99" for r in unmatched_paper_rows)

    def test_unmatched_sim_gets_missing_paper_flag(self) -> None:
        sim_extra = _sim(client_order_id="ord-orphan", intent_id=uuid4())
        result = _compare([_paper()], [_sim(), sim_extra])
        orphan_rows = [
            r for r in result.rows if DivergenceFlag.MISSING_PAPER_MATCH in r.divergence_flags
        ]
        assert any(r.client_order_id == "ord-orphan" for r in orphan_rows)

    def test_unmatched_paper_count_in_summary(self) -> None:
        paper2 = _paper(client_order_id="ord-2", intent_id=uuid4())
        result = _compare([_paper(), paper2], [_sim()])
        assert result.summary.unmatched_paper_orders == 1

    def test_unmatched_sim_count_in_summary(self) -> None:
        sim2 = _sim(client_order_id="ord-2", intent_id=uuid4())
        result = _compare([_paper()], [_sim(), sim2])
        assert result.summary.unmatched_simulated_orders == 1

    def test_all_matched_unmatched_counts_zero(self) -> None:
        result = _compare([_paper()], [_sim()])
        assert result.summary.unmatched_paper_orders == 0
        assert result.summary.unmatched_simulated_orders == 0

    def test_empty_paper_all_sim_unmatched(self) -> None:
        result = _compare([], [_sim(), _sim(client_order_id="ord-2", intent_id=uuid4())])
        assert result.summary.unmatched_simulated_orders == 2
        assert result.summary.matched_orders == 0

    def test_empty_sim_all_paper_unmatched(self) -> None:
        result = _compare([_paper(), _paper(client_order_id="ord-2", intent_id=uuid4())], [])
        assert result.summary.unmatched_paper_orders == 2
        assert result.summary.matched_orders == 0

    def test_both_empty_produces_empty_report(self) -> None:
        result = _compare([], [])
        assert result.rows == []
        assert result.summary.total_paper_orders == 0
        assert result.summary.total_simulated_orders == 0


# ── Matching priority ──────────────────────────────────────────────────────────


class TestMatchingPriority:
    def test_matches_by_intent_id_when_client_order_id_differs(self) -> None:
        paper = _paper(client_order_id="paper-ord", intent_id=_INTENT_A)
        sim = _sim(client_order_id="sim-ord", intent_id=_INTENT_A)  # same intent_id
        result = _compare([paper], [sim])
        assert result.summary.matched_orders == 1
        assert result.rows[0].divergence_flags == []

    def test_falls_back_to_client_order_id_when_intent_id_missing(self) -> None:
        paper = _paper(client_order_id="shared-id", intent_id=None)
        sim = _sim(client_order_id="shared-id", intent_id=None)
        result = _compare([paper], [sim])
        assert result.summary.matched_orders == 1

    def test_no_match_when_both_keys_differ(self) -> None:
        paper = _paper(client_order_id="paper-ord", intent_id=_INTENT_A)
        sim = _sim(client_order_id="sim-ord", intent_id=_INTENT_B)
        result = _compare([paper], [sim])
        assert result.summary.unmatched_paper_orders == 1
        assert result.summary.unmatched_simulated_orders == 1


# ── TWAP / VWAP policy matching (D-01 parent-child) ──────────────────────────


class TestPolicyAwareMatching:
    """TWAP/VWAP child orders from D-01 SimulatedExecutionModel are matched
    by parent_intent_id and aggregated into one comparison row."""

    def _make_twap_sims(
        self,
        parent_intent_id: str,
        parent_client_order_id: str,
        num_slices: int = 3,
        qty_per_slice: float = 333.0,
        price: float = 100.0,
    ) -> list[SimulatedFillRecord]:
        return [
            _sim(
                client_order_id=f"{parent_client_order_id}-slice-{i}",
                filled_qty=qty_per_slice,
                fill_price=price,
                intent_id=uuid4(),
                parent_intent_id=parent_intent_id,
                slice_index=i,
                execution_policy="twap",
            )
            for i in range(num_slices)
        ]

    def test_twap_slices_aggregate_to_one_row(self) -> None:
        parent = _paper(intent_id=_INTENT_A, filled_qty=1000.0, execution_policy="twap")
        slices = self._make_twap_sims(
            parent_intent_id=str(_INTENT_A),
            parent_client_order_id="ord-1",
            num_slices=4,
            qty_per_slice=250.0,
        )
        result = _compare([parent], slices)
        # One paper order → one output row (slices aggregated)
        paper_rows = [r for r in result.rows if r.client_order_id == "ord-1"]
        assert len(paper_rows) == 1

    def test_twap_aggregated_qty_matches_total(self) -> None:
        parent = _paper(intent_id=_INTENT_A, filled_qty=1000.0)
        slices = self._make_twap_sims(
            parent_intent_id=str(_INTENT_A),
            parent_client_order_id="ord-1",
            num_slices=4,
            qty_per_slice=250.0,
        )
        result = _compare([parent], slices)
        row = result.rows[0]
        assert row.simulated_qty == Decimal("1000")

    def test_twap_aggregated_price_is_weighted_average(self) -> None:
        parent = _paper(intent_id=_INTENT_A, filled_qty=1000.0, avg_fill_price=100.0)
        # 2 slices: 200 @ $99 and 800 @ $101 → weighted avg = (200*99 + 800*101) / 1000 = 100.6
        slices = [
            _sim(
                client_order_id="ord-1-s0",
                filled_qty=200.0,
                fill_price=99.0,
                intent_id=uuid4(),
                parent_intent_id=str(_INTENT_A),
                slice_index=0,
            ),
            _sim(
                client_order_id="ord-1-s1",
                filled_qty=800.0,
                fill_price=101.0,
                intent_id=uuid4(),
                parent_intent_id=str(_INTENT_A),
                slice_index=1,
            ),
        ]
        result = _compare([parent], slices)
        row = result.rows[0]
        expected_wav = Decimal("100.6")
        assert row.simulated_price is not None
        assert abs(row.simulated_price - expected_wav) < Decimal("0.01")

    def test_twap_matched_slice_count_recorded(self) -> None:
        parent = _paper(intent_id=_INTENT_A, filled_qty=1000.0)
        slices = self._make_twap_sims(
            parent_intent_id=str(_INTENT_A),
            parent_client_order_id="ord-1",
            num_slices=5,
            qty_per_slice=200.0,
        )
        result = _compare([parent], slices)
        assert result.rows[0].matched_slice_count == 5

    def test_twap_no_policy_slice_divergence_when_qty_matches(self) -> None:
        parent = _paper(intent_id=_INTENT_A, filled_qty=1000.0)
        slices = self._make_twap_sims(
            parent_intent_id=str(_INTENT_A),
            parent_client_order_id="ord-1",
            num_slices=5,
            qty_per_slice=200.0,
        )
        result = _compare([parent], slices)
        assert DivergenceFlag.POLICY_SLICE_DIVERGENCE not in result.rows[0].divergence_flags

    def test_twap_policy_slice_divergence_when_qty_mismatches(self) -> None:
        # Paper total = 1000, sim total = 700 → significant qty divergence from slices
        parent = _paper(intent_id=_INTENT_A, filled_qty=1000.0)
        slices = self._make_twap_sims(
            parent_intent_id=str(_INTENT_A),
            parent_client_order_id="ord-1",
            num_slices=4,
            qty_per_slice=175.0,  # total = 700, not 1000
        )
        result = _compare([parent], slices)
        flags = result.rows[0].divergence_flags
        assert DivergenceFlag.POLICY_SLICE_DIVERGENCE in flags

    def test_parent_intent_id_preserved_in_row(self) -> None:
        parent = _paper(intent_id=_INTENT_A, filled_qty=999.0)
        slices = self._make_twap_sims(
            parent_intent_id=str(_INTENT_A),
            parent_client_order_id="ord-1",
            num_slices=3,
            qty_per_slice=333.0,
        )
        result = _compare([parent], slices)
        # parent_intent_id from sim slices is str(_INTENT_A)
        assert result.rows[0].parent_intent_id == str(_INTENT_A)


# ── Summary metrics ────────────────────────────────────────────────────────────


class TestSummaryMetrics:
    def test_total_counts_correct(self) -> None:
        p1 = _paper(client_order_id="o1", intent_id=_INTENT_A)
        p2 = _paper(client_order_id="o2", intent_id=_INTENT_B)
        s1 = _sim(client_order_id="o1", intent_id=_INTENT_A)
        s2 = _sim(client_order_id="o2", intent_id=_INTENT_B)
        result = _compare([p1, p2], [s1, s2])
        assert result.summary.total_paper_orders == 2
        assert result.summary.total_simulated_orders == 2
        assert result.summary.matched_orders == 2

    def test_avg_price_diff_bps_computed(self) -> None:
        # Paper=$100, Sim=$101 → 100 bps. Two identical rows → avg = 100 bps.
        p1 = _paper(client_order_id="o1", intent_id=_INTENT_A, avg_fill_price=100.0)
        p2 = _paper(client_order_id="o2", intent_id=_INTENT_B, avg_fill_price=100.0)
        s1 = _sim(client_order_id="o1", intent_id=_INTENT_A, fill_price=101.0)
        s2 = _sim(client_order_id="o2", intent_id=_INTENT_B, fill_price=101.0)
        result = _compare([p1, p2], [s1, s2])
        assert result.summary.avg_price_diff_bps == Decimal("100.00")

    def test_avg_slippage_diff_bps_computed(self) -> None:
        p1 = _paper(client_order_id="o1", intent_id=_INTENT_A, slippage_bps=5.0)
        s1 = _sim(client_order_id="o1", intent_id=_INTENT_A, slippage_bps=7.0)
        result = _compare([p1], [s1])
        assert result.summary.avg_slippage_diff_bps == Decimal("2.00")

    def test_avg_latency_diff_ms_computed(self) -> None:
        ts = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        ts2 = ts + timedelta(seconds=2)
        p1 = _paper(client_order_id="o1", intent_id=_INTENT_A, fill_timestamp=ts)
        s1 = _sim(client_order_id="o1", intent_id=_INTENT_A, fill_timestamp=ts2)
        result = _compare([p1], [s1])
        assert result.summary.avg_latency_diff_ms == 2000

    def test_fill_rate_paper_computed(self) -> None:
        p = _paper(filled_qty=900.0, requested_qty=1000.0)
        result = _compare([p], [_sim()])
        assert result.summary.fill_rate_paper == Decimal("0.9000")

    def test_fill_rate_simulated_computed(self) -> None:
        s = _sim(filled_qty=500.0, requested_qty=1000.0)
        result = _compare([_paper()], [s])
        assert result.summary.fill_rate_simulated == Decimal("0.5000")

    def test_fill_rates_none_when_no_requested_qty(self) -> None:
        result = _compare([_paper(requested_qty=None)], [_sim(requested_qty=None)])
        assert result.summary.fill_rate_paper is None
        assert result.summary.fill_rate_simulated is None

    def test_divergence_count_correct(self) -> None:
        p1 = _paper(client_order_id="o1", intent_id=_INTENT_A, avg_fill_price=100.0)
        p2 = _paper(client_order_id="o2", intent_id=_INTENT_B, avg_fill_price=100.0)
        s1 = _sim(client_order_id="o1", intent_id=_INTENT_A, fill_price=101.0)  # divergent
        s2 = _sim(client_order_id="o2", intent_id=_INTENT_B, fill_price=100.0)  # exact
        result = _compare([p1, p2], [s1, s2])
        assert result.summary.divergence_count == 1


# ── Deterministic output ───────────────────────────────────────────────────────


class TestDeterministicOutput:
    def _make_records(self) -> tuple[list[PaperFillRecord], list[SimulatedFillRecord]]:
        papers = [
            _paper(client_order_id="o2", intent_id=_INTENT_B, avg_fill_price=101.0),
            _paper(client_order_id="o1", intent_id=_INTENT_A, avg_fill_price=100.0),
        ]
        sims = [
            _sim(client_order_id="o1", intent_id=_INTENT_A, fill_price=100.5),
            _sim(client_order_id="o2", intent_id=_INTENT_B, fill_price=101.5),
        ]
        return papers, sims

    def test_row_order_is_stable_by_client_order_id(self) -> None:
        papers, sims = self._make_records()
        r1 = _compare(papers, sims)
        r2 = _compare(papers, sims)
        assert [r.client_order_id for r in r1.rows] == [r.client_order_id for r in r2.rows]

    def test_row_order_sorted_by_client_order_id(self) -> None:
        papers, sims = self._make_records()
        result = _compare(papers, sims)
        client_ids = [r.client_order_id for r in result.rows]
        assert client_ids == sorted(client_ids[: len(papers)])  # paper rows come first

    def test_divergence_flags_are_sorted_within_each_row(self) -> None:
        # Force multiple flags: price divergence + qty divergence
        p = _paper(avg_fill_price=100.0, filled_qty=1000.0)
        s = _sim(fill_price=110.0, filled_qty=500.0)  # both price and qty differ
        result = _compare([p], [s])
        flags = result.rows[0].divergence_flags
        assert flags == sorted(flags)

    def test_same_inputs_same_comparison_id(self) -> None:
        papers, sims = self._make_records()
        r1 = _compare(papers, sims, strategy_id="strat-a", symbol="AAPL", start_time=_START)
        r2 = _compare(papers, sims, strategy_id="strat-a", symbol="AAPL", start_time=_START)
        assert r1.comparison_id == r2.comparison_id

    def test_different_params_different_comparison_id(self) -> None:
        papers, sims = self._make_records()
        r1 = _compare(papers, sims, strategy_id="strat-a")
        r2 = _compare(papers, sims, strategy_id="strat-b")
        assert r1.comparison_id != r2.comparison_id

    def test_same_inputs_same_summary(self) -> None:
        papers, sims = self._make_records()
        r1 = _compare(papers, sims)
        r2 = _compare(papers, sims)
        assert r1.summary.avg_price_diff_bps == r2.summary.avg_price_diff_bps
        assert r1.summary.matched_orders == r2.summary.matched_orders


# ── Report metadata ────────────────────────────────────────────────────────────


class TestReportMetadata:
    def test_generated_at_is_clock_value(self) -> None:
        fixed = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        svc = SimulationVsPaperComparisonService(clock=lambda: fixed)
        result = svc.compare([], [])
        assert result.generated_at == fixed

    def test_strategy_id_embedded_in_report(self) -> None:
        result = _compare([], [], strategy_id="momentum-v1")
        assert result.strategy_id == "momentum-v1"

    def test_symbol_embedded_in_report(self) -> None:
        result = _compare([], [], symbol="TSLA")
        assert result.symbol == "TSLA"

    def test_policy_type_embedded_in_report(self) -> None:
        result = _compare([], [], policy_type="twap")
        assert result.policy_type == "twap"

    def test_time_window_embedded_in_report(self) -> None:
        result = _compare([], [], start_time=_START, end_time=_END)
        assert result.start_time == _START
        assert result.end_time == _END


# ── Runtime separation ─────────────────────────────────────────────────────────


class TestRuntimeSeparation:
    def test_comparison_service_has_no_broker_dependency(self) -> None:
        import inspect

        from autonomous_trading_platform.application.services import (
            simulation_vs_paper_comparison_service,
        )

        source = inspect.getsource(simulation_vs_paper_comparison_service)
        assert "AlpacaBrokerClient" not in source
        assert "alpaca_broker_client" not in source

    def test_comparison_service_has_no_simulated_fill_service_dependency(self) -> None:
        import inspect

        from autonomous_trading_platform.application.services import (
            simulation_vs_paper_comparison_service,
        )

        source = inspect.getsource(simulation_vs_paper_comparison_service)
        assert "SimulatedExecutionService" not in source
        assert "simulated_execution_service" not in source

    def test_comparison_service_has_no_simulated_execution_engine_dependency(self) -> None:
        import inspect

        from autonomous_trading_platform.application.services import (
            simulation_vs_paper_comparison_service,
        )

        source = inspect.getsource(simulation_vs_paper_comparison_service)
        assert "SimulationExecutionEngine" not in source

    def test_service_instantiates_without_any_dependencies(self) -> None:
        svc = SimulationVsPaperComparisonService()
        assert svc is not None

    def test_service_runs_without_io(self) -> None:
        svc = _svc()
        result = svc.compare(
            [_paper()],
            [_sim()],
        )
        assert isinstance(result, SimulationVsPaperComparison)


# ── Multiple orders ────────────────────────────────────────────────────────────


class TestMultipleOrders:
    def test_multiple_matched_orders(self) -> None:
        papers = [
            _paper(client_order_id=f"o{i}", intent_id=UUID(f"{i:08d}-0000-0000-0000-000000000001"))
            for i in range(5)
        ]
        sims = [
            _sim(client_order_id=f"o{i}", intent_id=UUID(f"{i:08d}-0000-0000-0000-000000000001"))
            for i in range(5)
        ]
        result = _compare(papers, sims)
        assert result.summary.matched_orders == 5
        assert result.summary.unmatched_paper_orders == 0
        assert result.summary.unmatched_simulated_orders == 0

    def test_mixed_matched_and_unmatched(self) -> None:
        p1 = _paper(client_order_id="o1", intent_id=_INTENT_A)
        p2 = _paper(client_order_id="o2", intent_id=_INTENT_B)
        s1 = _sim(client_order_id="o1", intent_id=_INTENT_A)
        s3 = _sim(client_order_id="o3", intent_id=_INTENT_C)  # no paper match
        result = _compare([p1, p2], [s1, s3])
        assert result.summary.matched_orders == 1
        assert result.summary.unmatched_paper_orders == 1
        assert result.summary.unmatched_simulated_orders == 1
        assert len(result.rows) == 3  # p1+s1, p2 unmatched, s3 unmatched
