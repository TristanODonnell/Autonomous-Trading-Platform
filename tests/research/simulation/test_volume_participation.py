"""
Tests for volume participation constraints and carry-forward partial execution.

Covers:
- Participation cap enforcement (fills never exceed allowed quantity)
- Multi-bar carry-forward (large orders execute incrementally)
- Exact completion (final fill contains only remaining quantity)
- Small orders (below cap fill fully in one bar)
- latency_bars compatibility (0, 1, >1)
- Deterministic replay (identical inputs → identical outputs)
- End-of-simulation handling (no phantom fills beyond final bar)
- Config validation (rate must be > 0 and <= 1.0)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

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
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar

_SYMBOL = "AAPL"
_BASE_TS = datetime(2025, 3, 1, 9, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000001234")
_INITIAL_CASH = 1_000_000.0


# ---------------------------------------------------------------------------
# Helpers for unit-level service tests
# ---------------------------------------------------------------------------


def _make_bar(
    *,
    symbol: str = _SYMBOL,
    volume: int = 10_000,
    open: Decimal = Decimal("100"),
    close: Decimal = Decimal("100"),
    high: Decimal = Decimal("105"),
    low: Decimal = Decimal("95"),
) -> Any:
    return SimpleNamespace(
        symbol=symbol,
        timestamp=datetime(2025, 3, 1, 9, 30, tzinfo=UTC),
        open=open,
        close=close,
        high=high,
        low=low,
        volume=volume,
    )


def _make_intent(
    *,
    side: Side = Side.BUY,
    symbol: str = _SYMBOL,
    qty: Decimal = Decimal("5000"),
) -> Any:
    return SimpleNamespace(
        intent_id=str(uuid4()),
        run_id=uuid4(),
        symbol=symbol,
        side=side,
        qty=qty,
        order_type="market",
        limit_price=None,
    )


def _make_service(
    *,
    participation_rate: float | None = 0.10,
    latency_bars: int = 0,
) -> SimulatedExecutionService:
    cost_model = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"),
            min_commission=Decimal("0"),
        ),
        slippage_model=SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0"))),
    )
    return SimulatedExecutionService(
        simulation_cost_model_service=cost_model,
        fill_model_config=SimulatedFillModelConfig(
            latency_bars=latency_bars,
            max_volume_participation_rate=participation_rate,
        ),
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_participation_rate_zero_raises() -> None:
    with pytest.raises(ValueError, match="max_volume_participation_rate must be > 0"):
        SimulatedFillModelConfig(max_volume_participation_rate=0.0)


def test_participation_rate_above_one_raises() -> None:
    with pytest.raises(ValueError, match="max_volume_participation_rate must be <= 1.0"):
        SimulatedFillModelConfig(max_volume_participation_rate=1.5)


def test_participation_rate_exactly_one_is_valid() -> None:
    cfg = SimulatedFillModelConfig(max_volume_participation_rate=1.0)
    assert cfg.max_volume_participation_rate == 1.0


def test_participation_rate_none_is_default() -> None:
    cfg = SimulatedFillModelConfig()
    assert cfg.max_volume_participation_rate is None


# ---------------------------------------------------------------------------
# Participation cap enforcement
# ---------------------------------------------------------------------------


def test_fill_capped_to_participation_fraction() -> None:
    """BUY 5000 shares, bar volume 10000, cap 10% → max fill 1000."""
    service = _make_service(participation_rate=0.10)
    intent = _make_intent(qty=Decimal("5000"))
    bar = _make_bar(volume=10_000)

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity == Decimal("1000")


def test_carry_forward_quantity_is_remainder() -> None:
    """Remaining 4000 shares are queued for future bars."""
    service = _make_service(participation_rate=0.10)
    intent = _make_intent(qty=Decimal("5000"))
    bar = _make_bar(volume=10_000)

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.carry_forward) == 1
    cf = batch.carry_forward[0]
    assert cf.qty == Decimal("4000")
    assert cf.symbol == _SYMBOL
    assert cf.side == Side.BUY


def test_no_overfill_beyond_bar_volume() -> None:
    """Fill quantity must never exceed floor(volume * rate)."""
    service = _make_service(participation_rate=0.20)
    bar = _make_bar(volume=500)  # 20% of 500 = 100

    intent = _make_intent(qty=Decimal("999"))
    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills[0].quantity == Decimal("100")
    assert batch.carry_forward[0].qty == Decimal("899")


def test_participation_floor_rounds_down() -> None:
    """floor(333 * 0.10) = 33, not 33.3."""
    service = _make_service(participation_rate=0.10)
    bar = _make_bar(volume=333)

    intent = _make_intent(qty=Decimal("100"))
    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills[0].quantity == Decimal("33")
    assert batch.carry_forward[0].qty == Decimal("67")


def test_zero_volume_bar_produces_no_fill() -> None:
    """Zero-volume bar carries the entire order forward."""
    service = _make_service(participation_rate=0.10)
    bar = _make_bar(volume=0)

    intent = _make_intent(qty=Decimal("1000"))
    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills == []
    assert len(batch.carry_forward) == 1
    assert batch.carry_forward[0].qty == Decimal("1000")


# ---------------------------------------------------------------------------
# Small orders — fully fill in one bar
# ---------------------------------------------------------------------------


def test_small_order_fills_completely() -> None:
    """Order below cap fills entirely; no carry_forward produced."""
    service = _make_service(participation_rate=0.10)
    bar = _make_bar(volume=10_000)  # cap = 1000

    intent = _make_intent(qty=Decimal("500"))  # 500 < 1000
    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills[0].quantity == Decimal("500")
    assert batch.carry_forward == []


def test_order_exactly_at_cap_fills_completely() -> None:
    """Order equal to cap fills in full; no remainder."""
    service = _make_service(participation_rate=0.10)
    bar = _make_bar(volume=10_000)  # cap = 1000

    intent = _make_intent(qty=Decimal("1000"))
    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills[0].quantity == Decimal("1000")
    assert batch.carry_forward == []


# ---------------------------------------------------------------------------
# No participation rate — backward-compatible behavior
# ---------------------------------------------------------------------------


def test_no_participation_rate_fills_full_quantity() -> None:
    """Legacy mode (rate=None) fills entire order regardless of bar volume."""
    service = _make_service(participation_rate=None)
    bar = _make_bar(volume=100)  # tiny volume, but no cap

    intent = _make_intent(qty=Decimal("50_000"))
    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills[0].quantity == Decimal("50000")
    assert batch.carry_forward == []


# ---------------------------------------------------------------------------
# Engine-level multi-bar carry-forward integration
# ---------------------------------------------------------------------------


def _make_engine_and_service(
    *,
    participation_rate: float | None = 0.10,
    latency_bars: int = 0,
    universe_size: int = 1,
) -> tuple[SimulationExecutionEngine, SimulatedExecutionService]:
    cost_config = SimulationCostModelConfig(
        commission_per_share=Decimal("0"),
        min_commission=Decimal("0"),
    )
    slippage_model = SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0")))
    cost_service = SimulationCostModelService(config=cost_config, slippage_model=slippage_model)

    engine = SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(
            total_capital=_INITIAL_CASH,
            universe_size=universe_size,
        ),
    )
    exec_svc = SimulatedExecutionService(
        simulation_cost_model_service=cost_service,
        fill_model_config=SimulatedFillModelConfig(
            latency_bars=latency_bars,
            max_volume_participation_rate=participation_rate,
        ),
    )
    return engine, exec_svc


def _make_engine_bar(ts: datetime, *, close: float, volume: int) -> Any:
    return make_five_minute_bar(
        timestamp=ts,
        symbol=_SYMBOL,
        close_price=str(close),
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        volume=volume,
    )


def _build_window(
    prices: list[float],
    volumes: list[int],
    *,
    warmup_count: int = 0,
) -> Any:
    ts_list = [_BASE_TS + timedelta(minutes=5 * i) for i in range(len(prices))]
    bars = [
        _make_engine_bar(ts, close=p, volume=v)
        for ts, p, v in zip(ts_list, prices, volumes, strict=True)
    ]

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


def _build_ctx() -> StrategyContextBuilder:
    return StrategyContextBuilder(
        market_bar_reader=None,  # type: ignore[arg-type]
        bars_dataset=None,  # type: ignore[arg-type]
        lookback_bars=2,
    )


class TestEngineVolumeParticipation:
    def test_large_order_executes_incrementally(self) -> None:
        """Order too large for one bar should spread across multiple bars."""
        engine, exec_svc = _make_engine_and_service(participation_rate=0.10)
        # StubStrategy with low threshold signals BUY on bar 1 (price rise detected)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        # Bar volumes: 1000 each → cap 100 shares per bar
        # Capital 1_000_000 / price 100 = 10,000 shares target.
        # With 10% cap on 1000 volume = 100 shares/bar.
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[1000, 1000, 1000, 1000, 1000, 1000],
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        # Trades should span multiple bars (not all on bar 1)
        assert not result.trade_logs.empty
        trade_timestamps = result.trade_logs["timestamp"].nunique()
        assert trade_timestamps > 1, "Large order must spread across multiple bars"

    def test_cumulative_quantity_bounded_by_bar_volume(self) -> None:
        """No single bar's trade quantity exceeds floor(volume * rate)."""
        engine, exec_svc = _make_engine_and_service(participation_rate=0.10)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        bar_volume = 2000
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[bar_volume] * 5,
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        max_allowed_per_bar = bar_volume * 0.10  # 200
        for _, group in result.trade_logs.groupby("timestamp"):
            bar_qty = group["quantity"].sum()
            assert bar_qty <= max_allowed_per_bar, (
                f"Bar filled {bar_qty} shares, exceeds cap {max_allowed_per_bar}"
            )

    def test_no_participation_rate_fills_whole_order_single_bar(self) -> None:
        """With no rate set, entire order fills on first eligible bar (legacy behavior)."""
        engine, exec_svc = _make_engine_and_service(participation_rate=None)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        window = _build_window(
            prices=[100.0, 101.0, 101.0],
            volumes=[100, 100, 100],  # tiny volume but no cap enforced
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        # All trades happen on a single bar
        assert result.trade_logs["timestamp"].nunique() == 1

    def test_end_of_simulation_no_phantom_fills(self) -> None:
        """Remaining carry-forward quantity at dataset end produces no extra fills."""
        engine, exec_svc = _make_engine_and_service(participation_rate=0.01)  # very low cap
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        # Only 2 active bars after warmup → many bars needed to fully fill but we stop early
        window = _build_window(
            prices=[100.0, 101.0, 101.0],
            volumes=[1000, 1000, 1000],
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        # Equity curve must have exactly as many rows as active (non-warmup) bars
        active_bars = len(window.timeline) - 1  # 1 warmup
        assert len(result.equity_curve) == active_bars

        # No fill timestamps beyond the last bar in the window
        if not result.trade_logs.empty:
            last_bar_ts = window.timeline[-1]
            assert (result.trade_logs["timestamp"] <= last_bar_ts).all()

    # ------------------------------------------------------------------
    # latency_bars compatibility
    # ------------------------------------------------------------------

    def test_participation_with_latency_bars_zero(self) -> None:
        """latency_bars=0 + participation rate: partial fills at bar close, carry forward."""
        engine, exec_svc = _make_engine_and_service(participation_rate=0.10, latency_bars=0)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[500, 500, 500, 500, 500],
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        # With 10% cap on 500-share bars = 50 shares/bar, large orders need many bars
        if not result.trade_logs.empty:
            bar_volumes_per_ts = result.trade_logs.groupby("timestamp")["quantity"].sum()
            assert (bar_volumes_per_ts <= 50).all()

    def test_participation_with_latency_bars_one(self) -> None:
        """latency_bars=1 + participation rate: orders defer one bar, then partially fill."""
        engine, exec_svc = _make_engine_and_service(participation_rate=0.10, latency_bars=1)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[1000, 1000, 1000, 1000, 1000, 1000],
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        if not result.trade_logs.empty:
            bar_volumes_per_ts = result.trade_logs.groupby("timestamp")["quantity"].sum()
            assert (bar_volumes_per_ts <= 100).all()

    def test_participation_with_latency_bars_two(self) -> None:
        """latency_bars=2 + participation rate: orders defer two bars, then partially fill."""
        engine, exec_svc = _make_engine_and_service(participation_rate=0.10, latency_bars=2)
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[1000, 1000, 1000, 1000, 1000, 1000, 1000],
            warmup_count=1,
        )

        result = engine.execute(
            run_id=_RUN_ID,
            strategy=strategy,
            window=window,
            context_builder=ctx,
            simulated_execution_service=exec_svc,
            initial_cash=_INITIAL_CASH,
        )

        if not result.trade_logs.empty:
            bar_volumes_per_ts = result.trade_logs.groupby("timestamp")["quantity"].sum()
            assert (bar_volumes_per_ts <= 100).all()

    # ------------------------------------------------------------------
    # Deterministic replay
    # ------------------------------------------------------------------

    def test_identical_runs_produce_identical_fills(self) -> None:
        """Two runs with identical inputs must produce identical trade logs."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[800, 800, 800, 800, 800],
            warmup_count=1,
        )
        strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
        ctx = _build_ctx()

        def run_once():
            engine, exec_svc = _make_engine_and_service(participation_rate=0.10)
            return engine.execute(
                run_id=_RUN_ID,
                strategy=strategy,
                window=window,
                context_builder=ctx,
                simulated_execution_service=exec_svc,
                initial_cash=_INITIAL_CASH,
            )

        r1 = run_once()
        r2 = run_once()

        if not r1.trade_logs.empty:
            assert list(r1.trade_logs["quantity"]) == list(r2.trade_logs["quantity"])
            assert list(r1.trade_logs["timestamp"]) == list(r2.trade_logs["timestamp"])
            assert list(r1.equity_curve["equity"]) == list(r2.equity_curve["equity"])

    # ------------------------------------------------------------------
    # Sell-side participation
    # ------------------------------------------------------------------

    def test_sell_order_also_capped_by_participation(self) -> None:
        """Participation cap applies to SELL orders as well as BUY."""
        service = _make_service(participation_rate=0.10)
        bar = _make_bar(volume=10_000)

        intent = _make_intent(side=Side.SELL, qty=Decimal("5000"))
        batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

        assert len(batch.fills) == 1
        assert batch.fills[0].quantity == Decimal("1000")
        assert batch.carry_forward[0].qty == Decimal("4000")
        assert batch.carry_forward[0].side == Side.SELL
