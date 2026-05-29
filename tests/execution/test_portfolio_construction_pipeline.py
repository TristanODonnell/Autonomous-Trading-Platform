# tests/execution/test_portfolio_construction_pipeline.py
"""
Tests for the Portfolio Construction Pipeline — Recommendation 6.5.

Covers:
- Phase 1: signal collection → SignalBatch
- Phase 2: aggregation → PortfolioSignal list with attribution preserved
- Phase 3: constraint application → SignalIntent list
- Phase 4: order intent generation
- Full pipeline integration
- New netting policies (ALLOCATION_WEIGHTED, CONFIDENCE_WEIGHTED, aliases)
- Gross/net reconciliation diagnostics
- Observability events emitted
- Backward compatibility of Signal contract extensions
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from autonomous_trading_platform.application.services.portfolio_construction_pipeline import (
    PortfolioConstraintConfig,
    PortfolioConstructionPipeline,
    _active_position_symbols,
)
from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.portfolio_signal import (
    ConstraintApplicationStatus,
    PortfolioConstructionResult,
    PortfolioSignal,
    SignalBatch,
)
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.contracts.trading.signal_aggregate import (
    SignalNettingPolicy,
)
from autonomous_trading_platform.execution.services.portfolio_signal_aggregator import (
    PortfolioSignalAggregator,
    _build_contributions,
    _compute_signal_exposure,
    _has_conflict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 15, 14, 0, 0, tzinfo=UTC)
BAR_TS = datetime(2026, 1, 15, 13, 55, 0, tzinfo=UTC)
RUN_ID = uuid.uuid4()


def _signal(
    strategy_id: str,
    symbol: str,
    direction: SignalDirection,
    confidence: float | None = 0.8,
    strategy_version: str | None = None,
    feature_snapshot_ref: str | None = None,
) -> Signal:
    return Signal(
        signal_id=uuid.uuid4(),
        run_id=RUN_ID,
        timestamp=NOW,
        bar_timestamp=BAR_TS,
        strategy_id=strategy_id,
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        strategy_version=strategy_version,
        feature_snapshot_ref=feature_snapshot_ref,
    )


def _fake_order_intent(symbol: str) -> OrderIntent:
    from autonomous_trading_platform.contracts.common.enums import (
        OrderType,
        Side,
        TimeInForce,
    )

    return OrderIntent(
        intent_id=uuid.uuid4(),
        idempotency_key=f"key-{symbol}",
        run_id=RUN_ID,
        strategy_id="portfolio",
        timestamp=NOW,
        bar_timestamp=BAR_TS,
        symbol=symbol,
        side=Side.BUY,
        qty=Decimal("10"),
        notional=None,
        order_type=OrderType.MARKET,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        extended_hours=False,
        client_order_id=f"portfolio-{symbol}-abc123",
    )


def _make_pipeline(
    policy: SignalNettingPolicy = SignalNettingPolicy.CONSERVATIVE,
    constraint_config: PortfolioConstraintConfig | None = None,
    order_intents_by_symbol: dict[str, OrderIntent] | None = None,
) -> PortfolioConstructionPipeline:
    """Create a pipeline with a fake construction service."""
    aggregator = PortfolioSignalAggregator(policy=policy)

    fake_construction = MagicMock()
    if order_intents_by_symbol:
        fake_construction.generate_order_intents.return_value = iter(
            order_intents_by_symbol.values()
        )
    else:
        fake_construction.generate_order_intents.return_value = iter([])

    return PortfolioConstructionPipeline(
        signal_aggregator=aggregator,
        portfolio_construction_service=fake_construction,
        constraint_config=constraint_config,
    )


# ---------------------------------------------------------------------------
# Signal contract backward compatibility
# ---------------------------------------------------------------------------


class TestSignalContractExtensions:
    def test_signal_without_new_fields_is_valid(self):
        """Existing code that creates Signal without new fields must not break."""
        s = Signal(
            signal_id=uuid.uuid4(),
            run_id=RUN_ID,
            timestamp=NOW,
            bar_timestamp=BAR_TS,
            strategy_id="momentum",
            symbol="AAPL",
            direction=SignalDirection.BUY,
        )
        assert s.strategy_version is None
        assert s.feature_snapshot_ref is None
        assert s.target_exposure is None

    def test_signal_with_new_enrichment_fields(self):
        s = _signal(
            "strat_a",
            "AAPL",
            SignalDirection.BUY,
            strategy_version="v2.1",
            feature_snapshot_ref="snap-xyz",
        )
        assert s.strategy_version == "v2.1"
        assert s.feature_snapshot_ref == "snap-xyz"


# ---------------------------------------------------------------------------
# Phase 1 — Signal Batch Collection
# ---------------------------------------------------------------------------


class TestPhase1Collect:
    def test_signal_batch_counts_are_correct(self):
        pipeline = _make_pipeline()
        signals_by_strategy = {
            "strategy_a": [
                _signal("strategy_a", "AAPL", SignalDirection.BUY),
                _signal("strategy_a", "MSFT", SignalDirection.BUY),
            ],
            "strategy_b": [
                _signal("strategy_b", "AAPL", SignalDirection.SELL),
            ],
        }
        batch, _ = pipeline._phase1_collect(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            now=NOW,
        )
        assert batch.strategy_count == 2
        assert batch.raw_signal_count == 3
        assert batch.symbol_count == 2
        assert set(batch.strategy_ids) == {"strategy_a", "strategy_b"}

    def test_empty_strategy_set_produces_empty_batch(self):
        pipeline = _make_pipeline()
        batch, raw = pipeline._phase1_collect(
            signals_by_strategy={},
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            now=NOW,
        )
        assert batch.raw_signal_count == 0
        assert batch.strategy_count == 0
        assert raw == {}


# ---------------------------------------------------------------------------
# Phase 2 — Signal Aggregation → PortfolioSignal
# ---------------------------------------------------------------------------


class TestPhase2Aggregate:
    def test_nonconflicting_signals_pass_through(self):
        pipeline = _make_pipeline(policy=SignalNettingPolicy.CONSERVATIVE)
        batch = SignalBatch(
            batch_id=uuid.uuid4(),
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            collected_at=BAR_TS,
            strategy_count=1,
            symbol_count=1,
            raw_signal_count=1,
            strategy_ids=["strategy_a"],
        )
        raw_signals = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
        }
        bundle, portfolio_signals = pipeline._phase2_aggregate(
            raw_signals=raw_signals,
            signal_batch=batch,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            prices={"AAPL": 150.0},
            allocation_weights=None,
        )
        assert len(portfolio_signals) == 1
        ps = portfolio_signals[0]
        assert ps.symbol == "AAPL"
        assert ps.net_direction == SignalDirection.BUY
        assert ps.conflict_detected is False
        assert ps.constraint_status == ConstraintApplicationStatus.PENDING

    def test_conflict_creates_flat_rejected_portfolio_signal(self):
        pipeline = _make_pipeline(policy=SignalNettingPolicy.CONSERVATIVE)
        batch = SignalBatch(
            batch_id=uuid.uuid4(),
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            collected_at=BAR_TS,
            strategy_count=2,
            symbol_count=1,
            raw_signal_count=2,
            strategy_ids=["strategy_a", "strategy_b"],
        )
        raw_signals = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.8)],
        }
        bundle, portfolio_signals = pipeline._phase2_aggregate(
            raw_signals=raw_signals,
            signal_batch=batch,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            prices={"AAPL": 150.0},
            allocation_weights=None,
        )
        # CONSERVATIVE suppresses; we should have a REJECTED FLAT portfolio signal
        assert any(
            ps.symbol == "AAPL"
            and ps.net_direction == SignalDirection.FLAT
            and ps.constraint_status == ConstraintApplicationStatus.REJECTED
            for ps in portfolio_signals
        )

    def test_attribution_preserved_after_netting(self):
        pipeline = _make_pipeline(policy=SignalNettingPolicy.NET)
        batch = SignalBatch(
            batch_id=uuid.uuid4(),
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            collected_at=BAR_TS,
            strategy_count=2,
            symbol_count=1,
            raw_signal_count=2,
            strategy_ids=["strategy_a", "strategy_b"],
        )
        raw_signals = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.BUY, 0.7)],
        }
        bundle, portfolio_signals = pipeline._phase2_aggregate(
            raw_signals=raw_signals,
            signal_batch=batch,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            prices={"AAPL": 150.0},
            allocation_weights=None,
        )
        buy_signal = next((ps for ps in portfolio_signals if ps.symbol == "AAPL"), None)
        assert buy_signal is not None
        # Attribution must include both strategies
        strategy_ids_in_attribution = {c.strategy_id for c in buy_signal.attribution}
        assert "strategy_a" in strategy_ids_in_attribution
        assert "strategy_b" in strategy_ids_in_attribution


# ---------------------------------------------------------------------------
# Phase 3 — Constraint Application
# ---------------------------------------------------------------------------


class TestPhase3Constrain:
    def _make_pending_portfolio_signal(
        self, symbol: str, direction: SignalDirection
    ) -> PortfolioSignal:
        return PortfolioSignal(
            portfolio_signal_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            symbol=symbol,
            net_direction=direction,
            net_confidence=0.8,
            contributing_strategy_ids=["strategy_a"],
            conflict_detected=False,
            attribution=[],
            netting_policy=SignalNettingPolicy.CONSERVATIVE,
            constraint_status=ConstraintApplicationStatus.PENDING,
        )

    def test_sell_signals_always_pass_constraints(self):
        pipeline = _make_pipeline(
            constraint_config=PortfolioConstraintConfig(max_new_positions_per_cycle=0)
        )
        ps = self._make_pending_portfolio_signal("AAPL", SignalDirection.SELL)
        intents = pipeline._phase3_constrain(
            portfolio_signals=[ps],
            positions={},
            prices={"AAPL": 150.0},
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            batch_id=uuid.uuid4(),
        )
        assert len(intents) == 1
        assert intents[0].constraint_status == ConstraintApplicationStatus.APPLIED

    def test_max_new_positions_per_cycle_enforced(self):
        pipeline = _make_pipeline(
            constraint_config=PortfolioConstraintConfig(max_new_positions_per_cycle=1)
        )
        signals = [
            self._make_pending_portfolio_signal("AAPL", SignalDirection.BUY),
            self._make_pending_portfolio_signal("MSFT", SignalDirection.BUY),
            self._make_pending_portfolio_signal("GOOG", SignalDirection.BUY),
        ]
        intents = pipeline._phase3_constrain(
            portfolio_signals=signals,
            positions={},
            prices={"AAPL": 100.0, "MSFT": 200.0, "GOOG": 150.0},
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            batch_id=uuid.uuid4(),
        )
        applied = [i for i in intents if i.constraint_status == ConstraintApplicationStatus.APPLIED]
        rejected = [
            i for i in intents if i.constraint_status == ConstraintApplicationStatus.REJECTED
        ]
        assert len(applied) == 1
        assert len(rejected) == 2
        assert "max_new_positions_per_cycle" in rejected[0].constraints_triggered

    def test_existing_position_does_not_count_as_new(self):
        pipeline = _make_pipeline(
            constraint_config=PortfolioConstraintConfig(max_new_positions_per_cycle=0)
        )
        # AAPL is an existing position, so BUY signal should still pass
        signals = [self._make_pending_portfolio_signal("AAPL", SignalDirection.BUY)]
        intents = pipeline._phase3_constrain(
            portfolio_signals=signals,
            positions={"AAPL": 50},  # existing position
            prices={"AAPL": 150.0},
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            batch_id=uuid.uuid4(),
        )
        assert intents[0].constraint_status == ConstraintApplicationStatus.APPLIED

    def test_gross_exposure_limit_enforced(self):
        pipeline = _make_pipeline(
            constraint_config=PortfolioConstraintConfig(max_gross_signal_exposure_usd=100.0)
        )
        signals = [
            self._make_pending_portfolio_signal("AAPL", SignalDirection.BUY),
            self._make_pending_portfolio_signal("MSFT", SignalDirection.BUY),
        ]
        intents = pipeline._phase3_constrain(
            portfolio_signals=signals,
            positions={},
            prices={"AAPL": 80.0, "MSFT": 80.0},  # 80 + 80 = 160 > 100 limit
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            batch_id=uuid.uuid4(),
        )
        applied = [i for i in intents if i.constraint_status == ConstraintApplicationStatus.APPLIED]
        rejected = [
            i for i in intents if i.constraint_status == ConstraintApplicationStatus.REJECTED
        ]
        assert len(applied) == 1
        assert len(rejected) == 1
        assert "max_gross_signal_exposure_usd" in rejected[0].constraints_triggered

    def test_already_rejected_signals_pass_through_as_rejected(self):
        pipeline = _make_pipeline()
        ps = PortfolioSignal(
            portfolio_signal_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            symbol="AAPL",
            net_direction=SignalDirection.FLAT,
            net_confidence=0.0,
            contributing_strategy_ids=[],
            conflict_detected=True,
            attribution=[],
            netting_policy=SignalNettingPolicy.CONSERVATIVE,
            constraint_status=ConstraintApplicationStatus.REJECTED,
        )
        intents = pipeline._phase3_constrain(
            portfolio_signals=[ps],
            positions={},
            prices={},
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            batch_id=uuid.uuid4(),
        )
        assert intents[0].constraint_status == ConstraintApplicationStatus.REJECTED
        assert "signal_suppressed_by_netting" in intents[0].constraints_triggered


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_full_pipeline_no_conflicts_produces_order_intents(self):
        order_intents = {"AAPL": _fake_order_intent("AAPL")}
        pipeline = _make_pipeline(
            policy=SignalNettingPolicy.CONSERVATIVE,
            order_intents_by_symbol=order_intents,
        )
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
        }
        result = pipeline.run(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={"AAPL": 150.0},
            now=NOW,
        )
        assert isinstance(result, PortfolioConstructionResult)
        assert result.diagnostics.raw_signal_count == 1
        assert result.diagnostics.strategy_count == 1
        assert result.diagnostics.final_order_count == 1

    def test_opposing_signals_netting_result_in_buy_order(self):
        """Strategy A: BUY AAPL (high confidence). Strategy B: SELL AAPL (low confidence). NET → BUY."""
        order_intents = {"AAPL": _fake_order_intent("AAPL")}
        pipeline = _make_pipeline(
            policy=SignalNettingPolicy.NET,
            order_intents_by_symbol=order_intents,
        )
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.1)],
        }
        result = pipeline.run(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={"AAPL": 150.0},
            now=NOW,
        )
        # NET policy: strong BUY (0.9) vs weak SELL (0.1) → net BUY should win
        assert result.diagnostics.conflict_count == 1
        buy_signals = [
            ps
            for ps in result.portfolio_signals
            if ps.symbol == "AAPL" and ps.net_direction == SignalDirection.BUY
        ]
        assert len(buy_signals) == 1

    def test_conservative_suppresses_conflicts(self):
        pipeline = _make_pipeline(policy=SignalNettingPolicy.SUPPRESS_CONFLICTS)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.8)],
        }
        result = pipeline.run(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={"AAPL": 150.0},
            now=NOW,
        )
        assert result.diagnostics.suppressed_count == 1
        # No executable orders should come from a suppressed signal
        assert result.diagnostics.final_order_count == 0

    def test_near_zero_net_signal_suppressed(self):
        """Equal BUY and SELL weights cancel out under NET policy."""
        pipeline = _make_pipeline(policy=SignalNettingPolicy.NET)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.5)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.5)],
        }
        result = pipeline.run(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={"AAPL": 150.0},
            now=NOW,
        )
        # Equal weights → near-zero net score → suppressed
        assert result.diagnostics.suppressed_count == 1

    def test_diagnostics_gross_net_exposure_populated(self):
        order_intents = {"AAPL": _fake_order_intent("AAPL")}
        pipeline = _make_pipeline(
            policy=SignalNettingPolicy.CONSERVATIVE,
            order_intents_by_symbol=order_intents,
        )
        signals_by_strategy = {
            "strategy_a": [
                _signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9),
                _signal("strategy_a", "MSFT", SignalDirection.BUY, 0.7),
            ],
        }
        result = pipeline.run(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={"AAPL": 150.0, "MSFT": 200.0},
            now=NOW,
        )
        assert result.diagnostics.raw_gross_signal_exposure > 0
        assert result.diagnostics.net_signal_exposure > 0
        assert result.diagnostics.construction_duration_seconds >= 0

    def test_attribution_preserved_in_signal_intents(self):
        """Signal intents must carry the attribution from their parent portfolio signals."""
        pipeline = _make_pipeline(policy=SignalNettingPolicy.NET)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.BUY, 0.7)],
        }
        result = pipeline.run(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={"AAPL": 150.0},
            now=NOW,
        )
        applied_intents = [
            i
            for i in result.signal_intents
            if i.symbol == "AAPL" and i.constraint_status == ConstraintApplicationStatus.APPLIED
        ]
        assert len(applied_intents) == 1
        strategy_ids = {c.strategy_id for c in applied_intents[0].attribution}
        assert "strategy_a" in strategy_ids
        assert "strategy_b" in strategy_ids

    def test_empty_signals_produces_empty_result(self):
        pipeline = _make_pipeline()
        result = pipeline.run(
            signals_by_strategy={},
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            positions={},
            prices={},
            now=NOW,
        )
        assert result.diagnostics.raw_signal_count == 0
        assert result.diagnostics.final_order_count == 0
        assert result.signal_intents == []


# ---------------------------------------------------------------------------
# New netting policies
# ---------------------------------------------------------------------------


class TestNewNettingPolicies:
    def test_allocation_weighted_uses_provided_weights(self):
        """ALLOCATION_WEIGHTED should weight by the provided allocation_weights dict."""
        aggregator = PortfolioSignalAggregator(policy=SignalNettingPolicy.ALLOCATION_WEIGHTED)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.9)],
        }
        # strategy_a has 90% allocation, strategy_b 10% → BUY should dominate
        allocation_weights = {"strategy_a": 0.9, "strategy_b": 0.1}
        bundle = aggregator.aggregate(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
            allocation_weights=allocation_weights,
        )
        assert len(bundle.reconciled_signals) == 1
        assert bundle.reconciled_signals[0].direction == SignalDirection.BUY

    def test_confidence_weighted_weights_by_confidence(self):
        """CONFIDENCE_WEIGHTED: signals with higher confidence dominate the net direction."""
        aggregator = PortfolioSignalAggregator(policy=SignalNettingPolicy.CONFIDENCE_WEIGHTED)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.95)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.05)],
        }
        bundle = aggregator.aggregate(
            signals_by_strategy=signals_by_strategy,
            run_id=RUN_ID,
            bar_timestamp=BAR_TS,
        )
        assert len(bundle.reconciled_signals) == 1
        assert bundle.reconciled_signals[0].direction == SignalDirection.BUY

    def test_suppress_conflicts_alias_matches_conservative(self):
        agg_sc = PortfolioSignalAggregator(policy=SignalNettingPolicy.SUPPRESS_CONFLICTS)
        agg_c = PortfolioSignalAggregator(policy=SignalNettingPolicy.CONSERVATIVE)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.8)],
        }
        bundle_sc = agg_sc.aggregate(signals_by_strategy, RUN_ID, BAR_TS)
        bundle_c = agg_c.aggregate(signals_by_strategy, RUN_ID, BAR_TS)
        assert bundle_sc.total_suppressed_symbols == bundle_c.total_suppressed_symbols
        assert len(bundle_sc.reconciled_signals) == len(bundle_c.reconciled_signals)

    def test_dominant_signal_alias_matches_dominant(self):
        agg_ds = PortfolioSignalAggregator(policy=SignalNettingPolicy.DOMINANT_SIGNAL)
        agg_d = PortfolioSignalAggregator(policy=SignalNettingPolicy.DOMINANT)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.3)],
        }
        bundle_ds = agg_ds.aggregate(signals_by_strategy, RUN_ID, BAR_TS)
        bundle_d = agg_d.aggregate(signals_by_strategy, RUN_ID, BAR_TS)
        assert len(bundle_ds.reconciled_signals) == len(bundle_d.reconciled_signals)
        assert bundle_ds.reconciled_signals[0].direction == bundle_d.reconciled_signals[0].direction

    def test_net_alias_matches_proportional(self):
        agg_net = PortfolioSignalAggregator(policy=SignalNettingPolicy.NET)
        agg_prop = PortfolioSignalAggregator(policy=SignalNettingPolicy.PROPORTIONAL)
        signals_by_strategy = {
            "strategy_a": [_signal("strategy_a", "AAPL", SignalDirection.BUY, 0.9)],
            "strategy_b": [_signal("strategy_b", "AAPL", SignalDirection.SELL, 0.1)],
        }
        bundle_net = agg_net.aggregate(signals_by_strategy, RUN_ID, BAR_TS)
        bundle_prop = agg_prop.aggregate(signals_by_strategy, RUN_ID, BAR_TS)
        # Both should produce the same directional outcome
        assert len(bundle_net.reconciled_signals) == len(bundle_prop.reconciled_signals)


# ---------------------------------------------------------------------------
# Attribution helpers
# ---------------------------------------------------------------------------


class TestAttributionHelpers:
    def test_build_contributions_equal_weights_default(self):
        signals = [
            _signal("a", "AAPL", SignalDirection.BUY, 0.5),
            _signal("b", "AAPL", SignalDirection.SELL, 0.5),
        ]
        contributions = _build_contributions(signals)
        total = sum(c.weight for c in contributions)
        assert abs(total - 1.0) < 1e-9

    def test_build_contributions_allocation_weighted(self):
        signals = [
            _signal("a", "AAPL", SignalDirection.BUY, 0.9),
            _signal("b", "AAPL", SignalDirection.SELL, 0.9),
        ]
        alloc = {"a": 0.9, "b": 0.1}
        contributions = _build_contributions(signals, allocation_weights=alloc)
        weight_a = next(c.weight for c in contributions if c.strategy_id == "a")
        weight_b = next(c.weight for c in contributions if c.strategy_id == "b")
        assert weight_a > weight_b
        assert abs(weight_a + weight_b - 1.0) < 1e-9

    def test_has_conflict_detects_buy_vs_sell(self):
        signals = [
            _signal("a", "AAPL", SignalDirection.BUY),
            _signal("b", "AAPL", SignalDirection.SELL),
        ]
        contributions = _build_contributions(signals)
        assert _has_conflict(contributions) is True

    def test_has_conflict_false_when_all_same_direction(self):
        signals = [
            _signal("a", "AAPL", SignalDirection.BUY),
            _signal("b", "AAPL", SignalDirection.BUY),
        ]
        contributions = _build_contributions(signals)
        assert _has_conflict(contributions) is False


# ---------------------------------------------------------------------------
# Gross/net exposure helpers
# ---------------------------------------------------------------------------


class TestExposureComputation:
    def test_exposure_with_prices(self):
        signals = [
            _signal("a", "AAPL", SignalDirection.BUY),
            _signal("a", "MSFT", SignalDirection.SELL),
        ]
        prices = {"AAPL": 100.0, "MSFT": 200.0}
        gross, net = _compute_signal_exposure(signals, prices)
        assert gross == pytest.approx(300.0)
        assert net == pytest.approx(-100.0)  # 100 - 200

    def test_exposure_without_prices_counts_signals(self):
        signals = [
            _signal("a", "AAPL", SignalDirection.BUY),
            _signal("a", "MSFT", SignalDirection.BUY),
            _signal("b", "GOOG", SignalDirection.SELL),
        ]
        gross, net = _compute_signal_exposure(signals, None)
        assert gross == pytest.approx(3.0)
        assert net == pytest.approx(1.0)  # 2 buy - 1 sell

    def test_empty_signals_returns_zeros(self):
        gross, net = _compute_signal_exposure([], {"AAPL": 100.0})
        assert gross == 0.0
        assert net == 0.0


# ---------------------------------------------------------------------------
# Active position symbols helper
# ---------------------------------------------------------------------------


class TestActivePositionSymbols:
    def test_zero_quantity_positions_excluded(self):
        positions = {"AAPL": 10, "MSFT": 0, "GOOG": 5}
        active = _active_position_symbols(positions)
        assert "AAPL" in active
        assert "GOOG" in active
        assert "MSFT" not in active

    def test_empty_positions_returns_empty_set(self):
        assert _active_position_symbols({}) == set()
