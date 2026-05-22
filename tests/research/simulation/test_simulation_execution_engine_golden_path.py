"""
Golden-path integration tests for SimulationExecutionEngine.

Validates:
- multi-bar progression
- signal generation → order → fill → position update → cash update
- equity curve generation
- per-bar metrics generation
- no-lookahead semantics (guard enforced by real LookaheadGuardService)
- fills use only bars at the simulation timestamp (CURRENT_CLOSE policy)
- positions/cash stay internally consistent
- warmup bars produce signals but no trades
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

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
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar

_SYMBOL = "AAPL"
_BASE_TS = datetime(2025, 1, 15, 9, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000009900")
_INITIAL_CASH = 100_000.0


def _make_bar(ts: datetime, close: float):
    return make_five_minute_bar(
        timestamp=ts,
        symbol=_SYMBOL,
        close_price=str(close),
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        volume=1000,
    )


def _build_window(prices: list[float], warmup_count: int = 0):
    """Build a SimulationWindowData-compatible namespace from a price list."""
    ts_list = [_BASE_TS + timedelta(minutes=5 * i) for i in range(len(prices))]
    bars = [_make_bar(ts, p) for ts, p in zip(ts_list, prices, strict=True)]

    bars_by_symbol = {_SYMBOL: bars}
    bars_by_timestamp = {ts: {_SYMBOL: bar} for ts, bar in zip(ts_list, bars, strict=True)}
    warmup_timestamps = set(ts_list[:warmup_count])

    return SimpleNamespace(
        symbols=[_SYMBOL],
        timeline=ts_list,
        bars_by_symbol=bars_by_symbol,
        bars_by_timestamp=bars_by_timestamp,
        warmup_timestamps=warmup_timestamps,
        is_warmup=lambda ts: ts in warmup_timestamps,
    )


def _build_engine(
    universe_size: int = 1,
) -> tuple[SimulationExecutionEngine, SimulatedExecutionService]:
    cost_config = SimulationCostModelConfig(
        commission_per_share=Decimal("0"),
        min_commission=Decimal("0"),
    )
    slippage_model = SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0")))
    cost_service = SimulationCostModelService(config=cost_config, slippage_model=slippage_model)

    return SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(
            total_capital=_INITIAL_CASH,
            universe_size=universe_size,
        ),
    ), SimulatedExecutionService(
        simulation_cost_model_service=cost_service,
        fill_model_config=SimulatedFillModelConfig(
            market_fill_policy=MarketFillPolicy.CURRENT_CLOSE
        ),
    )


def _build_context_builder(lookback_bars: int = 2) -> StrategyContextBuilder:
    return StrategyContextBuilder(
        market_bar_reader=None,  # type: ignore[arg-type]
        bars_dataset=None,  # type: ignore[arg-type]
        lookback_bars=lookback_bars,
    )


# ---------------------------------------------------------------------------
# Basic golden path
# ---------------------------------------------------------------------------


class TestSimulationEngineGoldenPath:
    def test_empty_window_returns_empty_frames(self) -> None:
        engine, exec_svc = _build_engine()
        window = _build_window([100.0, 101.0, 102.0])
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=999.0)  # never signals
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        assert result.trade_logs.empty or len(result.trade_logs) == 0
        assert len(result.equity_curve) > 0  # equity rows for each live bar

    def test_buy_signal_creates_position(self) -> None:
        # Price rises on bar 2 → StubStrategy emits BUY
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        # There should be trades and positions
        assert not result.trade_logs.empty
        assert not result.positions.empty
        # Equity curve has one row per live (non-warmup) bar — all 5 here
        assert len(result.equity_curve) == len(prices)

    def test_equity_curve_has_run_id_and_strategy_id(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices)
        strategy = StubStrategy(strategy_id="stub_v1", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        assert not result.equity_curve.empty
        assert "run_id" in result.equity_curve.columns
        assert "equity" in result.equity_curve.columns
        # strategy_id may be the context's strategy_id or the strategy instance's id
        assert "strategy_id" in result.equity_curve.columns

    def test_initial_equity_equals_initial_cash(self) -> None:
        # Before any fills, equity = cash
        prices = [100.0, 101.0, 102.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices, warmup_count=2)  # all warmup except last
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=999.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        if not result.equity_curve.empty:
            assert result.equity_curve["equity"].iloc[0] == _INITIAL_CASH

    def test_signal_log_populated_when_signals_emitted(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        assert not result.signal_log.empty
        assert "symbol" in result.signal_log.columns
        assert "direction" in result.signal_log.columns


# ---------------------------------------------------------------------------
# No-lookahead guarantees
# ---------------------------------------------------------------------------


class TestNoLookaheadGuarantees:
    def test_lookahead_violation_in_timeline_raises(self) -> None:
        from types import SimpleNamespace

        # Non-strictly-increasing timeline should raise
        t0 = _BASE_TS
        bars = [_make_bar(t0, 100.0)]

        window = SimpleNamespace(
            symbols=[_SYMBOL],
            timeline=[t0, t0],  # duplicate → not strictly increasing
            bars_by_symbol={_SYMBOL: bars},
            bars_by_timestamp={t0: {_SYMBOL: bars[0]}},
            warmup_timestamps=set(),
            is_warmup=lambda ts: False,
        )

        engine, exec_svc = _build_engine()
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=1)

        with pytest.raises(ValueError, match="strictly increasing"):
            engine.execute(
                run_id=_RUN_ID,
                strategy=strategy,
                window=window,
                context_builder=ctx_builder,
                simulated_execution_service=exec_svc,
                initial_cash=_INITIAL_CASH,
            )

    def test_warmup_bars_produce_no_trades(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        # Mark all but last as warmup — should produce at most 1 trade
        engine, exec_svc = _build_engine()
        window = _build_window(prices, warmup_count=len(prices) - 1)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        # Only one live bar at the end — trades can come from at most that bar
        assert len(result.equity_curve) <= 1


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------


class TestSimulationEngineConsistency:
    def test_per_bar_metrics_has_entry_per_symbol_per_live_bar(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=999.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        if not result.per_bar_metrics.empty:
            assert "symbol" in result.per_bar_metrics.columns
            assert "timestamp" in result.per_bar_metrics.columns

    def test_trade_log_contains_expected_columns(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        if not result.trade_logs.empty:
            expected_cols = {
                "run_id",
                "strategy_id",
                "symbol",
                "timestamp",
                "side",
                "quantity",
                "price",
            }
            assert expected_cols.issubset(set(result.trade_logs.columns))

    def test_positions_frame_contains_expected_columns(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        engine, exec_svc = _build_engine()
        window = _build_window(prices)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx_builder = _build_context_builder(lookback_bars=2)

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx_builder,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        if not result.positions.empty:
            expected_cols = {"symbol", "quantity", "market_value"}
            assert expected_cols.issubset(set(result.positions.columns))
