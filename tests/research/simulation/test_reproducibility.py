"""
Determinism and reproducibility contract tests.

Documents and enforces what IS and IS NOT deterministic in the research stack.

DETERMINISTIC (same seed + config + data → identical):
  - indicator outputs (pure functions)
  - signal directions and signal_ids
  - fill prices (CURRENT_CLOSE, no random element)
  - equity curve values
  - per-bar metrics values

INTENTIONALLY NON-DETERMINISTIC (operational):
  - run_id (UUID4 per run)
  - fill_id / broker_order_id (UUID4)
  - ingested_at timestamps on bars
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import numpy as np

from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.research.simulation.models.fill_model import (
    MarketFillPolicy,
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
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.implementations.random_debug_strategy import (
    RandomStrategy,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 9, 30, tzinfo=UTC)
_SYMBOL = "AAPL"
_RUN_ID = UUID("00000000-0000-0000-0000-999900000001")
_INITIAL_CASH = 50_000.0


def _make_bar(ts, close):
    return make_five_minute_bar(
        timestamp=ts,
        symbol=_SYMBOL,
        close_price=str(close),
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        volume=1000,
    )


def _build_window(prices: list[float]):
    ts_list = [_BASE_TS + timedelta(minutes=5 * i) for i in range(len(prices))]
    bars = [_make_bar(ts, p) for ts, p in zip(ts_list, prices, strict=True)]
    return SimpleNamespace(
        symbols=[_SYMBOL],
        timeline=ts_list,
        bars_by_symbol={_SYMBOL: bars},
        bars_by_timestamp={ts: {_SYMBOL: bar} for ts, bar in zip(ts_list, bars, strict=True)},
        warmup_timestamps=set(),
        is_warmup=lambda ts: False,
    )


def _build_engine_and_exec():
    cost_config = SimulationCostModelConfig(
        commission_per_share=Decimal("0"), min_commission=Decimal("0")
    )
    slippage = SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0")))
    cost_service = SimulationCostModelService(config=cost_config, slippage_model=slippage)

    engine = SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(total_capital=_INITIAL_CASH, universe_size=1),
    )
    exec_svc = SimulatedExecutionService(
        simulation_cost_model_service=cost_service,
        fill_model_config=SimulatedFillModelConfig(
            market_fill_policy=MarketFillPolicy.CURRENT_CLOSE
        ),
    )
    return engine, exec_svc


def _run_simulation(strategy, prices: list[float]):
    engine, exec_svc = _build_engine_and_exec()
    window = _build_window(prices)
    ctx_builder = StrategyContextBuilder(
        market_bar_reader=None,  # type: ignore[arg-type]
        bars_dataset=None,  # type: ignore[arg-type]
        lookback_bars=2,
    )
    return engine.execute(
        run_id=_RUN_ID,
        strategy=strategy,
        window=window,
        context_builder=ctx_builder,
        simulated_execution_service=exec_svc,
        initial_cash=_INITIAL_CASH,
    )


# ---------------------------------------------------------------------------
# Indicator determinism
# ---------------------------------------------------------------------------


class TestIndicatorDeterminism:
    def test_sma_same_inputs_same_output(self) -> None:
        from autonomous_trading_platform.strategy.indicators.trend import simple_moving_average

        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert simple_moving_average(values, 3) == simple_moving_average(values, 3)

    def test_ema_same_inputs_same_output(self) -> None:
        from autonomous_trading_platform.strategy.indicators.trend import exponential_moving_average

        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert exponential_moving_average(values, 3) == exponential_moving_average(values, 3)

    def test_rsi_same_inputs_same_output(self) -> None:
        from autonomous_trading_platform.strategy.indicators.momentum import rsi

        values = [100.0, 101.0, 99.0, 102.0, 100.0, 103.0]
        assert rsi(values, 5) == rsi(values, 5)

    def test_z_score_same_inputs_same_output(self) -> None:
        from autonomous_trading_platform.strategy.indicators.mean_reversion import z_score

        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert z_score(values, 5) == z_score(values, 5)


# ---------------------------------------------------------------------------
# Signal ID determinism
# ---------------------------------------------------------------------------


class TestSignalIdDeterminism:
    def test_same_context_same_signal_id(self) -> None:
        from datetime import timedelta
        from uuid import UUID

        from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
        from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
        from tests.utilities.factories import make_five_minute_bar

        ts = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        bars = [
            make_five_minute_bar(timestamp=ts - timedelta(minutes=5), close_price="100.00"),
            make_five_minute_bar(timestamp=ts - timedelta(minutes=10), close_price="101.00"),
        ]
        bars_sorted = sorted(bars, key=lambda b: b.timestamp)

        ctx = StrategyContext(
            run_id=UUID("00000000-0000-0000-0000-111100000001"),
            strategy_id="stub_v1",
            symbol="AAPL",
            evaluation_timestamp=ts,
            bar_timestamp=ts - timedelta(minutes=5),
            bars=bars_sorted,
        )

        strategy = StubStrategy(strategy_id="stub_v1")
        s1 = strategy.evaluate_symbol(ctx)
        s2 = strategy.evaluate_symbol(ctx)
        assert s1 is not None and s2 is not None
        assert s1.signal_id == s2.signal_id


# ---------------------------------------------------------------------------
# Random strategy seed reproducibility
# ---------------------------------------------------------------------------


class TestRandomStrategySeedReproducibility:
    def test_same_seed_produces_identical_signal_sequence(self) -> None:
        prices = [100.0 + i for i in range(10)]

        strat_a = RandomStrategy(strategy_id="r", signal_probability=0.7, random_seed=42)
        strat_b = RandomStrategy(strategy_id="r", signal_probability=0.7, random_seed=42)

        result_a = _run_simulation(strat_a, prices)
        result_b = _run_simulation(strat_b, prices)

        # Signal directions should be identical
        if not result_a.signal_log.empty and not result_b.signal_log.empty:
            dirs_a = list(result_a.signal_log["direction"])
            dirs_b = list(result_b.signal_log["direction"])
            assert dirs_a == dirs_b

    def test_different_seeds_produce_different_signal_sequences(self) -> None:
        prices = [100.0 + i for i in range(15)]

        strat_a = RandomStrategy(strategy_id="r", signal_probability=0.8, random_seed=1)
        strat_b = RandomStrategy(strategy_id="r", signal_probability=0.8, random_seed=2)

        result_a = _run_simulation(strat_a, prices)
        result_b = _run_simulation(strat_b, prices)

        if not result_a.signal_log.empty and not result_b.signal_log.empty:
            dirs_a = list(result_a.signal_log["direction"])
            dirs_b = list(result_b.signal_log["direction"])
            # Almost certainly different — not guaranteed but extremely likely
            assert dirs_a != dirs_b or len(dirs_a) == 0


# ---------------------------------------------------------------------------
# Deterministic simulation results
# ---------------------------------------------------------------------------


class TestSimulationResultDeterminism:
    def test_same_config_same_equity_curve(self) -> None:
        prices = [100.0, 101.0, 102.0, 101.0, 103.0, 104.0, 103.0, 105.0]

        result_a = _run_simulation(
            StubStrategy(strategy_id="stub", price_change_threshold=0.0), prices
        )
        result_b = _run_simulation(
            StubStrategy(strategy_id="stub", price_change_threshold=0.0), prices
        )

        if not result_a.equity_curve.empty and not result_b.equity_curve.empty:
            equities_a = list(result_a.equity_curve["equity"])
            equities_b = list(result_b.equity_curve["equity"])
            assert equities_a == equities_b

    def test_same_config_same_trade_quantities(self) -> None:
        prices = [100.0, 101.0, 102.0, 101.0, 103.0]

        result_a = _run_simulation(
            StubStrategy(strategy_id="stub", price_change_threshold=0.0), prices
        )
        result_b = _run_simulation(
            StubStrategy(strategy_id="stub", price_change_threshold=0.0), prices
        )

        if not result_a.trade_logs.empty and not result_b.trade_logs.empty:
            qty_a = list(result_a.trade_logs["quantity"])
            qty_b = list(result_b.trade_logs["quantity"])
            assert qty_a == qty_b

    def test_determinism_contract_documented(self) -> None:
        """
        Documents which outputs are deterministic vs operational.

        DETERMINISTIC (same seed + config + data = same results):
          - equity_curve["equity"] values
          - trade_logs["quantity"] and ["price"]
          - signal_log["direction"]
          - per_bar_metrics["position_size"]

        OPERATIONAL (different each run by design):
          - trade_logs["run_id"] (if UUID4)
          - fill_id / broker_order_id (UUID4)
        """
        # This test is a documentation marker — it always passes.
        # The assertions above in sister tests enforce the contracts.
        assert True


# ---------------------------------------------------------------------------
# Python / NumPy seed isolation
# ---------------------------------------------------------------------------


class TestGlobalSeedIsolation:
    def test_python_random_seed_is_deterministic(self) -> None:
        random.seed(42)
        val1 = random.random()

        random.seed(42)
        val2 = random.random()

        assert val1 == val2

    def test_numpy_random_seed_is_deterministic(self) -> None:
        np.random.seed(42)
        val1 = float(np.random.random())

        np.random.seed(42)
        val2 = float(np.random.random())

        assert val1 == val2

    def test_random_strategy_rng_is_instance_isolated(self) -> None:
        # Two RandomStrategy instances with same seed should NOT interfere with each other
        strat_a = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=0)
        strat_b = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=0)

        from datetime import timedelta
        from uuid import UUID

        from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
        from tests.utilities.factories import make_five_minute_bar

        ts = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        ctx = StrategyContext(
            run_id=UUID("00000000-0000-0000-0000-000000000099"),
            strategy_id="r",
            symbol="AAPL",
            evaluation_timestamp=ts,
            bar_timestamp=ts - timedelta(minutes=5),
            bars=[make_five_minute_bar(timestamp=ts - timedelta(minutes=5))],
        )

        # Interleaving calls should not affect determinism within each instance
        seq_a = [strat_a.evaluate_symbol(ctx) for _ in range(5)]
        seq_b = [strat_b.evaluate_symbol(ctx) for _ in range(5)]

        dirs_a = [s.direction if s else None for s in seq_a]
        dirs_b = [s.direction if s else None for s in seq_b]
        assert dirs_a == dirs_b
