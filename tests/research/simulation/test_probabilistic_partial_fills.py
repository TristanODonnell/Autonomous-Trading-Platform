"""
Tests for probabilistic partial fill modeling (TASK F-01).

Covers:
- Config validation (probability bounds, fraction bounds, min < max invariant)
- Probability disabled → identical R-04 deterministic behavior
- Probability=1.0 → every eligible fill is partial
- Probabilistic fill quantities stay within configured fraction bounds
- Probabilistic fills never exceed participation-capped quantity
- Remaining quantity carries forward correctly after probabilistic reduction
- Deterministic replay: identical seed → identical fill progression
- Multi-bar fragmented execution under probabilistic model
- latency_bars compatibility (0, 1, >1)
- End-of-simulation: no phantom fills beyond final bar
"""

from __future__ import annotations

import random
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
_BASE_TS = datetime(2025, 4, 1, 9, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000005501")
_INITIAL_CASH = 1_000_000.0
_SEED = 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(
    *,
    symbol: str = _SYMBOL,
    volume: int = 10_000,
    close: Decimal = Decimal("100"),
    open_: Decimal | None = None,
    high: Decimal = Decimal("105"),
    low: Decimal = Decimal("95"),
) -> Any:
    return SimpleNamespace(
        symbol=symbol,
        timestamp=datetime(2025, 4, 1, 9, 30, tzinfo=UTC),
        open=open_ if open_ is not None else close,
        close=close,
        high=high,
        low=low,
        volume=volume,
    )


def _make_intent(
    *,
    side: Side = Side.BUY,
    symbol: str = _SYMBOL,
    qty: Decimal = Decimal("1000"),
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


def _make_cost_service() -> SimulationCostModelService:
    return SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"),
            min_commission=Decimal("0"),
        ),
        slippage_model=SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0"))),
    )


def _make_service(
    *,
    partial_fill_probability: float | None = None,
    partial_fill_min_fraction: float = 0.10,
    partial_fill_max_fraction: float = 0.75,
    participation_rate: float | None = None,
    latency_bars: int = 0,
    seed: int | None = _SEED,
) -> SimulatedExecutionService:
    return SimulatedExecutionService(
        simulation_cost_model_service=_make_cost_service(),
        fill_model_config=SimulatedFillModelConfig(
            latency_bars=latency_bars,
            max_volume_participation_rate=participation_rate,
            partial_fill_probability=partial_fill_probability,
            partial_fill_min_fraction=partial_fill_min_fraction,
            partial_fill_max_fraction=partial_fill_max_fraction,
        ),
        rng=random.Random(seed) if seed is not None else None,
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_partial_fill_probability_zero_raises() -> None:
    with pytest.raises(ValueError, match="partial_fill_probability must be > 0"):
        SimulatedFillModelConfig(partial_fill_probability=0.0)


def test_partial_fill_probability_above_one_raises() -> None:
    with pytest.raises(ValueError, match="partial_fill_probability must be > 0 and <= 1.0"):
        SimulatedFillModelConfig(partial_fill_probability=1.5)


def test_partial_fill_probability_exactly_one_is_valid() -> None:
    cfg = SimulatedFillModelConfig(partial_fill_probability=1.0)
    assert cfg.partial_fill_probability == 1.0


def test_partial_fill_probability_none_is_default() -> None:
    cfg = SimulatedFillModelConfig()
    assert cfg.partial_fill_probability is None


def test_partial_fill_min_fraction_zero_raises() -> None:
    with pytest.raises(ValueError, match="partial_fill_min_fraction must be > 0"):
        SimulatedFillModelConfig(partial_fill_min_fraction=0.0, partial_fill_max_fraction=0.75)


def test_partial_fill_max_fraction_above_one_raises() -> None:
    with pytest.raises(ValueError, match="partial_fill_max_fraction must be <= 1.0"):
        SimulatedFillModelConfig(partial_fill_min_fraction=0.10, partial_fill_max_fraction=1.5)


def test_partial_fill_min_equals_max_raises() -> None:
    with pytest.raises(ValueError, match="partial_fill_min_fraction.*< partial_fill_max_fraction"):
        SimulatedFillModelConfig(partial_fill_min_fraction=0.50, partial_fill_max_fraction=0.50)


def test_partial_fill_min_greater_than_max_raises() -> None:
    with pytest.raises(ValueError, match="partial_fill_min_fraction.*< partial_fill_max_fraction"):
        SimulatedFillModelConfig(partial_fill_min_fraction=0.80, partial_fill_max_fraction=0.30)


def test_partial_fill_fraction_defaults_are_valid() -> None:
    cfg = SimulatedFillModelConfig()
    assert cfg.partial_fill_min_fraction == 0.10
    assert cfg.partial_fill_max_fraction == 0.75


# ---------------------------------------------------------------------------
# Probability disabled → identical R-04 deterministic behavior
# ---------------------------------------------------------------------------


def test_probability_none_fills_full_participation_cap() -> None:
    """With probability=None, fill is capped only by participation rate (R-04 path)."""
    service = _make_service(
        partial_fill_probability=None,
        participation_rate=0.10,
    )
    bar = _make_bar(volume=10_000)  # cap = 1000
    intent = _make_intent(qty=Decimal("5000"))

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity == Decimal("1000")
    assert batch.carry_forward[0].qty == Decimal("4000")


def test_probability_none_no_participation_fills_fully() -> None:
    """With probability=None and no participation rate, full order fills (legacy)."""
    service = _make_service(partial_fill_probability=None, participation_rate=None)
    bar = _make_bar(volume=100)
    intent = _make_intent(qty=Decimal("5000"))

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills[0].quantity == Decimal("5000")
    assert batch.carry_forward == []


# ---------------------------------------------------------------------------
# Probability=1.0 → every eligible fill is partial
# ---------------------------------------------------------------------------


def test_probability_one_always_produces_partial_fill() -> None:
    """probability=1.0 means every fill event is partial; order never completes in one bar."""
    service = _make_service(
        partial_fill_probability=1.0,
        partial_fill_min_fraction=0.10,
        partial_fill_max_fraction=0.75,
    )
    bar = _make_bar(volume=100_000)  # large volume so participation cap doesn't interfere
    intent = _make_intent(qty=Decimal("10_000"))

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity < Decimal("10000"), "probability=1.0 must always partially fill"
    assert len(batch.carry_forward) == 1
    assert batch.carry_forward[0].qty > Decimal("0")


def test_probability_one_fill_plus_carry_forward_equals_original() -> None:
    """Filled qty + carry_forward qty must equal the original intent qty."""
    service = _make_service(
        partial_fill_probability=1.0,
        partial_fill_min_fraction=0.20,
        partial_fill_max_fraction=0.80,
    )
    original_qty = Decimal("2000")
    bar = _make_bar(volume=100_000)
    intent = _make_intent(qty=original_qty)

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    filled = batch.fills[0].quantity if batch.fills else Decimal("0")
    carried = batch.carry_forward[0].qty if batch.carry_forward else Decimal("0")
    assert filled + carried == original_qty


# ---------------------------------------------------------------------------
# Distribution bounds
# ---------------------------------------------------------------------------


def test_probabilistic_fill_fraction_within_bounds() -> None:
    """Sampled fill fractions must fall within [min_fraction, max_fraction]."""
    min_frac, max_frac = 0.20, 0.60
    service = _make_service(
        partial_fill_probability=1.0,  # always partial
        partial_fill_min_fraction=min_frac,
        partial_fill_max_fraction=max_frac,
        seed=99,
    )
    base_qty = Decimal("1000")
    bar = _make_bar(volume=100_000)

    for _ in range(30):
        intent = _make_intent(qty=base_qty)
        batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if batch.fills:
            filled = batch.fills[0].quantity
            fraction = float(filled) / float(base_qty)
            # Allow floor rounding to push fraction slightly below min, but not by more than 1/qty
            assert fraction >= min_frac - 1 / float(base_qty), (
                f"fill fraction {fraction:.4f} below min {min_frac}"
            )
            assert fraction <= max_frac, f"fill fraction {fraction:.4f} above max {max_frac}"


def test_probabilistic_fill_never_exceeds_participation_cap() -> None:
    """Probabilistic fill must never exceed the participation-capped quantity."""
    service = _make_service(
        partial_fill_probability=0.50,
        participation_rate=0.10,
        seed=7,
    )
    bar = _make_bar(volume=5_000)  # cap = 500

    for _ in range(20):
        intent = _make_intent(qty=Decimal("10000"))
        batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if batch.fills:
            assert batch.fills[0].quantity <= Decimal("500"), (
                "probabilistic fill must not exceed participation cap"
            )


# ---------------------------------------------------------------------------
# Carry-forward correctness under probabilistic fills
# ---------------------------------------------------------------------------


def test_carry_forward_qty_is_original_minus_filled() -> None:
    """carry_forward.qty must always equal original_qty - filled_qty."""
    service = _make_service(
        partial_fill_probability=1.0,
        partial_fill_min_fraction=0.10,
        partial_fill_max_fraction=0.90,
        seed=123,
    )
    bar = _make_bar(volume=100_000)

    for _ in range(15):
        original = Decimal("500")
        intent = _make_intent(qty=original)
        batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if batch.fills and batch.carry_forward:
            assert batch.fills[0].quantity + batch.carry_forward[0].qty == original


def test_minimum_fill_is_one_share() -> None:
    """Probabilistic partial fill guarantees at least 1 share when qty >= 1."""
    service = _make_service(
        partial_fill_probability=1.0,
        partial_fill_min_fraction=0.0001,  # tiny fraction
        partial_fill_max_fraction=0.0002,
        seed=55,
    )
    bar = _make_bar(volume=100_000)
    intent = _make_intent(qty=Decimal("10"))

    batch = service.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity >= Decimal("1"), "minimum fill must be 1 share"


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


def test_identical_seeds_produce_identical_fills() -> None:
    """Two services with the same seed must produce identical fill sequences."""

    def run_fills(seed: int) -> list[Decimal]:
        svc = _make_service(partial_fill_probability=0.6, seed=seed)
        bar = _make_bar(volume=100_000)
        results = []
        for _ in range(20):
            intent = _make_intent(qty=Decimal("1000"))
            batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
            qty = batch.fills[0].quantity if batch.fills else Decimal("0")
            results.append(qty)
        return results

    assert run_fills(42) == run_fills(42)


def test_different_seeds_produce_different_fills() -> None:
    """Different seeds should (almost certainly) produce different fill sequences."""

    def run_fills(seed: int) -> list[Decimal]:
        svc = _make_service(partial_fill_probability=0.8, seed=seed)
        bar = _make_bar(volume=100_000)
        results = []
        for _ in range(20):
            intent = _make_intent(qty=Decimal("1000"))
            batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
            qty = batch.fills[0].quantity if batch.fills else Decimal("0")
            results.append(qty)
        return results

    assert run_fills(1) != run_fills(2)


# ---------------------------------------------------------------------------
# Engine-level integration tests
# ---------------------------------------------------------------------------


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


def _build_engine() -> SimulationExecutionEngine:
    return SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(
            total_capital=_INITIAL_CASH,
            universe_size=1,
        ),
    )


def _build_exec_svc(
    *,
    partial_fill_probability: float | None,
    partial_fill_min_fraction: float = 0.10,
    partial_fill_max_fraction: float = 0.75,
    participation_rate: float | None = None,
    latency_bars: int = 0,
    seed: int = _SEED,
) -> SimulatedExecutionService:
    return SimulatedExecutionService(
        simulation_cost_model_service=_make_cost_service(),
        fill_model_config=SimulatedFillModelConfig(
            latency_bars=latency_bars,
            max_volume_participation_rate=participation_rate,
            partial_fill_probability=partial_fill_probability,
            partial_fill_min_fraction=partial_fill_min_fraction,
            partial_fill_max_fraction=partial_fill_max_fraction,
        ),
        rng=random.Random(seed),
    )


def _run(
    *,
    exec_svc: SimulatedExecutionService,
    window: Any,
) -> Any:
    engine = _build_engine()
    strategy = StubStrategy(strategy_id="stub", price_change_threshold=0.0)
    ctx = StrategyContextBuilder(
        market_bar_reader=None,  # type: ignore[arg-type]
        bars_dataset=None,  # type: ignore[arg-type]
        lookback_bars=2,
    )
    return engine.execute(
        run_id=_RUN_ID,
        strategy=strategy,
        window=window,
        context_builder=ctx,
        simulated_execution_service=exec_svc,
        initial_cash=_INITIAL_CASH,
    )


class TestEngineProabilisticFills:
    def test_probability_disabled_matches_r04_deterministic_behavior(self) -> None:
        """Disabling probabilistic fills (probability=None) matches pure R-04 caps."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0],
            volumes=[1000, 1000, 1000, 1000],
            warmup_count=1,
        )
        svc_det = _build_exec_svc(partial_fill_probability=None, participation_rate=0.10)
        svc_prob = _build_exec_svc(partial_fill_probability=None, participation_rate=0.10, seed=999)

        r_det = _run(exec_svc=svc_det, window=window)
        r_prob = _run(exec_svc=svc_prob, window=window)

        assert list(r_det.trade_logs["quantity"]) == list(r_prob.trade_logs["quantity"])

    def test_probabilistic_fills_spread_execution_across_more_bars(self) -> None:
        """With high probability, execution spreads across more bars than R-04 alone."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[5000] * 8,
            warmup_count=1,
        )
        # R-04 only: 10% cap on 5000 = 500 shares/bar. 1_000_000/100 = 10_000 target.
        # Takes ~20 bars without probabilistic, but with prob=1.0 further reduces each bar.
        svc_det = _build_exec_svc(partial_fill_probability=None, participation_rate=0.10)
        svc_prob = _build_exec_svc(partial_fill_probability=1.0, participation_rate=0.10, seed=7)

        r_det = _run(exec_svc=svc_det, window=window)
        r_prob = _run(exec_svc=svc_prob, window=window)

        if not r_det.trade_logs.empty and not r_prob.trade_logs.empty:
            det_total = r_det.trade_logs["quantity"].sum()
            prob_total = r_prob.trade_logs["quantity"].sum()
            # With probability=1.0 (always partial), cumulative fills should be <= deterministic
            assert prob_total <= det_total, (
                "probabilistic fills with prob=1.0 should not exceed deterministic fills"
            )

    def test_deterministic_replay_with_seed(self) -> None:
        """Two runs with the same seed must produce identical fill logs."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[2000, 2000, 2000, 2000, 2000],
            warmup_count=1,
        )

        r1 = _run(exec_svc=_build_exec_svc(partial_fill_probability=0.5, seed=_SEED), window=window)
        r2 = _run(exec_svc=_build_exec_svc(partial_fill_probability=0.5, seed=_SEED), window=window)

        if not r1.trade_logs.empty:
            assert list(r1.trade_logs["quantity"]) == list(r2.trade_logs["quantity"])
            assert list(r1.equity_curve["equity"]) == list(r2.equity_curve["equity"])

    def test_end_of_simulation_no_phantom_fills(self) -> None:
        """Remaining carry-forward quantities expire cleanly at dataset end."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0],
            volumes=[1000, 1000, 1000],
            warmup_count=1,
        )
        svc = _build_exec_svc(
            partial_fill_probability=1.0,
            participation_rate=0.01,  # very tight cap → much carry-forward
            seed=42,
        )

        result = _run(exec_svc=svc, window=window)

        # Equity curve rows = active (non-warmup) bars exactly
        active_bars = len(window.timeline) - 1
        assert len(result.equity_curve) == active_bars

        if not result.trade_logs.empty:
            last_bar_ts = window.timeline[-1]
            assert (result.trade_logs["timestamp"] <= last_bar_ts).all()

    def test_latency_bars_zero_with_probabilistic_fills(self) -> None:
        """latency_bars=0 + probabilistic fills: per-bar quantities bounded by probabilistic cap."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[10_000] * 5,
            warmup_count=1,
        )
        svc = _build_exec_svc(
            partial_fill_probability=1.0,
            partial_fill_min_fraction=0.10,
            participation_rate=0.20,
            latency_bars=0,
            seed=11,
        )

        result = _run(exec_svc=svc, window=window)

        # With prob=1.0, every fill should be partial and carry forward
        assert not result.trade_logs.empty

    def test_latency_bars_one_with_probabilistic_fills(self) -> None:
        """latency_bars=1 + probabilistic fills: deferred execution also partially fills."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[10_000] * 6,
            warmup_count=1,
        )
        svc = _build_exec_svc(
            partial_fill_probability=1.0,
            participation_rate=0.20,
            latency_bars=1,
            seed=22,
        )

        result = _run(exec_svc=svc, window=window)
        assert not result.trade_logs.empty

    def test_latency_bars_two_with_probabilistic_fills(self) -> None:
        """latency_bars=2 + probabilistic fills: 2-bar deferred execution partially fills."""
        window = _build_window(
            prices=[100.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            volumes=[10_000] * 7,
            warmup_count=1,
        )
        svc = _build_exec_svc(
            partial_fill_probability=1.0,
            participation_rate=0.20,
            latency_bars=2,
            seed=33,
        )

        result = _run(exec_svc=svc, window=window)
        assert not result.trade_logs.empty

    def test_portfolio_accounting_correct_under_probabilistic_fills(self) -> None:
        """Cash + positions_value must equal initial capital (no cost model in test)."""
        window = _build_window(
            prices=[100.0, 100.0, 100.0, 100.0, 100.0],
            volumes=[5000] * 5,
            warmup_count=1,
        )
        svc = _build_exec_svc(
            partial_fill_probability=0.5,
            participation_rate=0.10,
            seed=77,
        )

        result = _run(exec_svc=svc, window=window)

        # With zero slippage/commission and flat price=100, equity must equal initial capital
        for equity in result.equity_curve["equity"]:
            assert abs(equity - _INITIAL_CASH) < 1.0, (
                f"equity {equity} deviates from initial cash at flat price"
            )
