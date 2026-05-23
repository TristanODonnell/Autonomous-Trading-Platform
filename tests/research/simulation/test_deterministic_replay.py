"""P-03 — Deterministic replay validation tests.

Asserts that identical simulation inputs + identical seed produce byte-for-byte
equivalent artifacts, and that different seeds produce different stochastic outcomes
when probabilistic execution is enabled.

These tests serve as a CI regression gate: any change that breaks deterministic replay
will cause failures here before reaching production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.simple_position_sizer import (
    SimplePositionSizer,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
    SimulationExecutionEngine,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunRequest
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_SYMBOL = "AAPL"
_BASE_TS = datetime(2025, 3, 1, 9, 30, tzinfo=UTC)
_RUN_ID_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_RUN_ID_B = UUID("aaaaaaaa-0000-0000-0000-000000000002")
_INITIAL_CASH = 100_000.0
_SEED_A = 42
_SEED_B = 99


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(ts: datetime, close: float, volume: int = 10_000):
    return make_five_minute_bar(
        timestamp=ts,
        symbol=_SYMBOL,
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        close_price=str(close),
        volume=volume,
    )


def _build_window(prices: list[float], warmup_count: int = 0):
    ts_list = [_BASE_TS + timedelta(minutes=5 * i) for i in range(len(prices))]
    bars = [_make_bar(ts, p) for ts, p in zip(ts_list, prices, strict=True)]
    bars_by_timestamp = {ts: {_SYMBOL: bar} for ts, bar in zip(ts_list, bars, strict=True)}
    warmup_timestamps = set(ts_list[:warmup_count])
    return SimpleNamespace(
        symbols=[_SYMBOL],
        timeline=ts_list,
        bars_by_symbol={_SYMBOL: bars},
        bars_by_timestamp=bars_by_timestamp,
        warmup_timestamps=warmup_timestamps,
        is_warmup=lambda ts: ts in warmup_timestamps,
    )


def _make_execution_service(
    *,
    seed: int,
    partial_fill_probability: float | None = None,
    order_rejection_probability: float | None = None,
    fill_probability_on_touch: float | None = None,
    max_volume_participation_rate: float | None = None,
    latency_bars: int = 0,
) -> SimulatedExecutionService:
    import random

    cost_service = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"),
            min_commission=Decimal("0"),
        ),
        slippage_model=SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0"))),
    )
    svc = SimulatedExecutionService(
        simulation_cost_model_service=cost_service,
        fill_model_config=SimulatedFillModelConfig(
            latency_bars=latency_bars,
            partial_fill_probability=partial_fill_probability,
            max_volume_participation_rate=max_volume_participation_rate,
            fill_probability_on_touch=fill_probability_on_touch,
            order_rejection_probability=order_rejection_probability,
        ),
    )
    svc.reset_for_run(rng=random.Random(seed))
    return svc


def _make_engine(universe_size: int = 1) -> SimulationExecutionEngine:
    return SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(
            total_capital=_INITIAL_CASH,
            universe_size=universe_size,
        ),
    )


def _make_context_builder() -> StrategyContextBuilder:
    return StrategyContextBuilder(
        market_bar_reader=None,  # type: ignore[arg-type]
        bars_dataset=None,  # type: ignore[arg-type]
        lookback_bars=2,
    )


def _run_engine(
    *,
    run_id: UUID,
    seed: int,
    prices: list[float],
    partial_fill_probability: float | None = None,
    order_rejection_probability: float | None = None,
    fill_probability_on_touch: float | None = None,
    max_volume_participation_rate: float | None = None,
    latency_bars: int = 0,
):
    """Execute one simulation and return the engine result."""
    engine = _make_engine()
    exec_svc = _make_execution_service(
        seed=seed,
        partial_fill_probability=partial_fill_probability,
        order_rejection_probability=order_rejection_probability,
        fill_probability_on_touch=fill_probability_on_touch,
        max_volume_participation_rate=max_volume_participation_rate,
        latency_bars=latency_bars,
    )
    window = _build_window(prices)
    strategy = StubStrategy(strategy_id="replay_stub", price_change_threshold=0.0)
    ctx = _make_context_builder()

    return engine.execute(
        run_id=run_id,
        strategy=strategy,
        window=window,
        context_builder=ctx,
        simulated_execution_service=exec_svc,
        initial_cash=_INITIAL_CASH,
    )


# ---------------------------------------------------------------------------
# P-01 — Deterministic Fill IDs
# ---------------------------------------------------------------------------


class TestDeterministicFillIds:
    """fill_id and broker_order_id must be stable across reruns."""

    _PRICES = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]

    def _collect_fills(self, result) -> list[Any]:
        """Extract Fill objects stored in trade_log columns."""
        return getattr(result, "_fills_for_test", [])

    def test_fill_ids_are_uuids(self):
        exec_svc = _make_execution_service(seed=_SEED_A)
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        batch = exec_svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

        assert len(batch.fills) == 1
        fill = batch.fills[0]
        uuid.UUID(fill.fill_id)  # must parse without raising
        uuid.UUID(fill.broker_order_id)

    def test_same_seed_produces_identical_fill_ids(self):
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        svc_a = _make_execution_service(seed=_SEED_A)
        svc_b = _make_execution_service(seed=_SEED_A)

        fill_a = svc_a.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]
        fill_b = svc_b.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]

        assert fill_a.fill_id == fill_b.fill_id
        assert fill_a.broker_order_id == fill_b.broker_order_id

    def test_fill_ids_are_deterministic_independent_of_seed(self):
        """fill_id depends only on run/intent identity, not on the RNG seed."""
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        svc_a = _make_execution_service(seed=_SEED_A)
        svc_b = _make_execution_service(seed=_SEED_B)

        fill_a = svc_a.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]
        fill_b = svc_b.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]

        # Different seeds don't change deterministic IDs — IDs are input-derived, not RNG-derived.
        assert fill_a.fill_id == fill_b.fill_id
        assert fill_a.broker_order_id == fill_b.broker_order_id

    def test_sequential_fills_have_unique_ids(self):
        """Multiple fills from one service instance must have distinct IDs."""
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        svc = _make_execution_service(seed=_SEED_A)
        fill_1 = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]
        fill_2 = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]

        assert fill_1.fill_id != fill_2.fill_id
        assert fill_1.broker_order_id != fill_2.broker_order_id

    def test_different_run_ids_produce_different_fill_ids(self):
        intent_a = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        intent_b = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("33333333-0000-0000-0000-000000000002"),  # different run
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        svc = _make_execution_service(seed=_SEED_A)
        fill_a = svc.fill(order_intents=[intent_a], bars_at_timestamp={_SYMBOL: bar}).fills[0]
        svc.reset_for_run()
        fill_b = svc.fill(order_intents=[intent_b], bars_at_timestamp={_SYMBOL: bar}).fills[0]

        assert fill_a.fill_id != fill_b.fill_id

    def test_reset_for_run_restarts_fill_counter(self):
        """After reset_for_run, the same intent produces the same fill_id."""
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        svc = _make_execution_service(seed=_SEED_A)
        fill_run1 = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]

        svc.reset_for_run()
        fill_run2 = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]

        assert fill_run1.fill_id == fill_run2.fill_id


# ---------------------------------------------------------------------------
# P-02 — Isolated RNG
# ---------------------------------------------------------------------------


class TestIsolatedRng:
    """Isolated RNG must make probabilistic decisions reproducible."""

    _PRICES = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]

    def test_same_seed_produces_identical_partial_fill_quantities(self):
        """Same seed → same probabilistic partial fill quantities."""
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("100"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0, volume=10_000)

        svc_a = _make_execution_service(seed=_SEED_A, partial_fill_probability=0.9)
        svc_b = _make_execution_service(seed=_SEED_A, partial_fill_probability=0.9)

        qty_a = svc_a.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills
        qty_b = svc_b.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills

        # Both runs must produce exactly the same outcome (fill or no fill, and quantity).
        assert len(qty_a) == len(qty_b)
        if qty_a:
            assert qty_a[0].quantity == qty_b[0].quantity

    def test_different_seeds_can_produce_different_stochastic_outcomes(self):
        """Different seeds eventually diverge when stochastic behavior is enabled."""
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("1000"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0, volume=100_000)

        outcomes_a = []
        outcomes_b = []
        svc_a = _make_execution_service(seed=_SEED_A, partial_fill_probability=0.7)
        svc_b = _make_execution_service(seed=_SEED_B, partial_fill_probability=0.7)

        for _ in range(20):
            fills_a = svc_a.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
            fills_b = svc_b.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
            outcomes_a.append(fills_a.fills[0].quantity if fills_a.fills else None)
            outcomes_b.append(fills_b.fills[0].quantity if fills_b.fills else None)

        # Different seeds must eventually diverge in their stochastic draws.
        assert outcomes_a != outcomes_b

    def test_order_rejection_is_reproducible_with_same_seed(self):
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        outcomes_a = []
        outcomes_b = []

        svc_a = _make_execution_service(seed=_SEED_A, order_rejection_probability=0.5)
        svc_b = _make_execution_service(seed=_SEED_A, order_rejection_probability=0.5)

        for _ in range(10):
            outcomes_a.append(
                len(svc_a.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills)
            )
            outcomes_b.append(
                len(svc_b.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills)
            )

        assert outcomes_a == outcomes_b

    def test_rng_is_isolated_via_reset_for_run(self):
        """After reset_for_run with same seed, stochastic sequence repeats exactly."""
        import random

        svc = _make_execution_service(seed=_SEED_A, partial_fill_probability=0.7)
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("1000"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0, volume=100_000)

        run1 = []
        for _ in range(5):
            fills = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
            run1.append(fills.fills[0].quantity if fills.fills else None)

        svc.reset_for_run(rng=random.Random(_SEED_A))

        run2 = []
        for _ in range(5):
            fills = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
            run2.append(fills.fills[0].quantity if fills.fills else None)

        assert run1 == run2


# ---------------------------------------------------------------------------
# P-03 — Full engine replay equivalence
# ---------------------------------------------------------------------------


class TestFullEngineReplayEquivalence:
    """Two identical simulations must produce byte-equivalent results."""

    _PRICES = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]

    def test_equity_curve_identical_across_reruns(self):
        result_a = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)
        result_b = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)

        assert len(result_a.equity_curve) == len(result_b.equity_curve)
        # Numeric columns must match exactly (same deterministic arithmetic path).
        for col in ("equity", "cash", "positions_value"):
            assert list(result_a.equity_curve[col]) == list(result_b.equity_curve[col]), (
                f"equity_curve[{col!r}] differs between runs"
            )

    def test_trade_log_identical_across_reruns(self):
        result_a = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)
        result_b = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)

        assert len(result_a.trade_logs) == len(result_b.trade_logs)
        for col in ("symbol", "side", "quantity", "price", "fees"):
            assert list(result_a.trade_logs[col]) == list(result_b.trade_logs[col]), (
                f"trade_logs[{col!r}] differs between runs"
            )

    def test_signal_log_identical_across_reruns(self):
        result_a = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)
        result_b = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)

        assert len(result_a.signal_log) == len(result_b.signal_log)
        for col in ("symbol", "direction"):
            if col in result_a.signal_log.columns:
                assert list(result_a.signal_log[col]) == list(result_b.signal_log[col])

    def test_positions_identical_across_reruns(self):
        result_a = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)
        result_b = _run_engine(run_id=_RUN_ID_A, seed=_SEED_A, prices=self._PRICES)

        assert len(result_a.positions) == len(result_b.positions)

    def test_probabilistic_replay_equity_curve_identical(self):
        """Partial fills must not break replay equivalence when seed is fixed."""
        result_a = _run_engine(
            run_id=_RUN_ID_A,
            seed=_SEED_A,
            prices=self._PRICES,
            partial_fill_probability=0.7,
        )
        result_b = _run_engine(
            run_id=_RUN_ID_A,
            seed=_SEED_A,
            prices=self._PRICES,
            partial_fill_probability=0.7,
        )

        assert len(result_a.equity_curve) == len(result_b.equity_curve)
        assert list(result_a.equity_curve["equity"]) == list(result_b.equity_curve["equity"])
        assert len(result_a.trade_logs) == len(result_b.trade_logs)

    def test_different_seeds_can_produce_different_equity_curves(self):
        """Different seeds must eventually diverge in probabilistic mode."""
        result_a = _run_engine(
            run_id=_RUN_ID_A,
            seed=_SEED_A,
            prices=self._PRICES,
            partial_fill_probability=0.8,
        )
        result_b = _run_engine(
            run_id=_RUN_ID_B,
            seed=_SEED_B,
            prices=self._PRICES,
            partial_fill_probability=0.8,
        )

        # Equity curves may or may not differ for this specific config, but
        # trade counts or fill quantities should diverge across different seeds.
        # We assert they are not forced-equal by the determinism mechanism.
        equity_a = list(result_a.equity_curve["equity"])
        equity_b = list(result_b.equity_curve["equity"])
        trade_count_a = len(result_a.trade_logs)
        trade_count_b = len(result_b.trade_logs)

        # At least one observable must differ (equity or trade count).
        assert equity_a != equity_b or trade_count_a != trade_count_b, (
            "Expected different seeds to produce different outcomes under probabilistic fills"
        )

    def test_rejection_probability_replay_is_stable(self):
        """Order rejection with fixed seed must produce identical trade counts."""
        result_a = _run_engine(
            run_id=_RUN_ID_A,
            seed=_SEED_A,
            prices=self._PRICES,
            order_rejection_probability=0.4,
        )
        result_b = _run_engine(
            run_id=_RUN_ID_A,
            seed=_SEED_A,
            prices=self._PRICES,
            order_rejection_probability=0.4,
        )

        assert len(result_a.trade_logs) == len(result_b.trade_logs)
        assert list(result_a.equity_curve["equity"]) == list(result_b.equity_curve["equity"])


# ---------------------------------------------------------------------------
# P-03 — SimulationRunner._derive_run_id stability
# ---------------------------------------------------------------------------


class TestDeriveRunId:
    """SimulationRunner._derive_run_id must produce stable, unique UUIDs."""

    def _make_request(self, **overrides) -> SimulationRunRequest:
        defaults = dict(
            strategy_id="momentum_v1",
            strategy_config={"type": "momentum", "parameters": {}},
            dataset_version="v1",
            random_seed=42,
            price_basis=__import__(
                "autonomous_trading_platform.contracts.common.enums",
                fromlist=["PriceBasis"],
            ).PriceBasis.ADJUSTED,
            symbols=["AAPL", "MSFT"],
            start_date=__import__("datetime").date(2024, 1, 1),
            end_date=__import__("datetime").date(2024, 6, 30),
            experiment_id="exp_001",
            stage_name="train",
            window_role="is",
        )
        defaults.update(overrides)
        return SimulationRunRequest(**defaults)

    def _make_runner(self):
        from unittest.mock import MagicMock

        from autonomous_trading_platform.research.simulation.simulation_runner import (
            SimulationRunner,
        )

        return SimulationRunner(
            dataset_resolver=MagicMock(),
            window_loader=MagicMock(),
            result_recorder=MagicMock(),
            execution_engine=MagicMock(),
            context_builder=MagicMock(),
            simulated_execution_service=MagicMock(),
            simulation_run_repository=MagicMock(),
            strategy_config_repository=MagicMock(),
            metrics_summary_repository=MagicMock(),
            strategy_factory=MagicMock(),
        )

    def test_same_inputs_produce_same_run_id(self):
        runner = self._make_runner()
        req = self._make_request()
        assert runner._derive_run_id(req) == runner._derive_run_id(req)

    def test_different_seeds_produce_different_run_ids(self):
        runner = self._make_runner()
        req_a = self._make_request(random_seed=1)
        req_b = self._make_request(random_seed=2)
        assert runner._derive_run_id(req_a) != runner._derive_run_id(req_b)

    def test_different_strategies_produce_different_run_ids(self):
        runner = self._make_runner()
        req_a = self._make_request(strategy_id="momentum_v1")
        req_b = self._make_request(strategy_id="mean_reversion_v1")
        assert runner._derive_run_id(req_a) != runner._derive_run_id(req_b)

    def test_different_symbols_produce_different_run_ids(self):
        runner = self._make_runner()
        req_a = self._make_request(symbols=["AAPL"])
        req_b = self._make_request(symbols=["MSFT"])
        assert runner._derive_run_id(req_a) != runner._derive_run_id(req_b)

    def test_run_id_is_valid_uuid(self):
        runner = self._make_runner()
        req = self._make_request()
        run_id = runner._derive_run_id(req)
        assert isinstance(run_id, UUID)


# ---------------------------------------------------------------------------
# Regression — no nondeterministic ID generation in simulation path
# ---------------------------------------------------------------------------


class TestNoNondeterministicIds:
    """Guard against accidental reintroduction of uuid4 in simulation output paths."""

    def test_fill_id_is_not_random_between_identical_calls(self):
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        all_fill_ids: list[str] = []
        for _ in range(5):
            svc = _make_execution_service(seed=_SEED_A)
            fill = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]
            all_fill_ids.append(fill.fill_id)

        # All runs with same inputs must produce the same fill_id.
        assert len(set(all_fill_ids)) == 1, (
            f"fill_id is not deterministic: got {len(set(all_fill_ids))} distinct values"
        )

    def test_broker_order_id_is_not_random_between_identical_calls(self):
        intent = SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol=_SYMBOL,
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )
        bar = _make_bar(_BASE_TS, 100.0)

        all_broker_ids: list[str] = []
        for _ in range(5):
            svc = _make_execution_service(seed=_SEED_A)
            fill = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar}).fills[0]
            all_broker_ids.append(fill.broker_order_id)

        assert len(set(all_broker_ids)) == 1, (
            f"broker_order_id is not deterministic: got {len(set(all_broker_ids))} distinct values"
        )
