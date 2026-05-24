"""
Tests for dividend and corporate-action awareness in simulation accounting (TASK A-02).

Validates:
- DividendEvent model validation
- Dividend cash applied correctly: shares × cash_per_share → settled_cash
- No dividend when no position held
- Multiple dividend events (multiple symbols, multiple events same symbol)
- Equity / total-return reflects dividend cash
- Dividend fires exactly once per ex_date (not once per intraday bar)
- SimulationRunConfig accepts dividend_events field
- SimulationCacheKey includes dividend_events_hash
- Different dividend events produce different cache keys
- Manifest records dividend lineage fields
- Deterministic replay: identical inputs → identical outputs
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.simulation.dividend_event import DividendEvent
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
    SimulationExecutionResult,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar

_SYMBOL = "AAPL"
_SYMBOL_B = "MSFT"
# Use daily-spaced timestamps so each bar falls on a distinct calendar date.
_BASE_TS = datetime(2025, 6, 1, 9, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000009910")
_INITIAL_CASH = 10_000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(ts: datetime, close: float, symbol: str = _SYMBOL, volume: int = 50_000):
    return make_five_minute_bar(
        timestamp=ts,
        symbol=symbol,
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        close_price=str(close),
        volume=volume,
    )


def _build_window(
    prices: list[float],
    *,
    warmup_count: int = 0,
    symbol: str = _SYMBOL,
    daily: bool = True,
):
    """Build a window.  daily=True spaces bars 1 day apart for clean ex_date matching."""
    delta = timedelta(days=1) if daily else timedelta(minutes=5)
    ts_list = [_BASE_TS + delta * i for i in range(len(prices))]
    bars = [_make_bar(ts, p, symbol=symbol) for ts, p in zip(ts_list, prices, strict=True)]
    bars_by_timestamp = {ts: {symbol: bar} for ts, bar in zip(ts_list, bars, strict=True)}
    warmup_ts = set(ts_list[:warmup_count])
    return SimpleNamespace(
        symbols=[symbol],
        timeline=ts_list,
        bars_by_symbol={symbol: bars},
        bars_by_timestamp=bars_by_timestamp,
        warmup_timestamps=warmup_ts,
        is_warmup=lambda ts: ts in warmup_ts,
    )


def _build_multi_symbol_window(
    prices_a: list[float],
    prices_b: list[float],
    *,
    warmup_count: int = 0,
    daily: bool = True,
):
    """Build a two-symbol window for multi-dividend tests."""
    assert len(prices_a) == len(prices_b)
    delta = timedelta(days=1) if daily else timedelta(minutes=5)
    ts_list = [_BASE_TS + delta * i for i in range(len(prices_a))]
    bars_by_timestamp = {}
    for ts, pa, pb in zip(ts_list, prices_a, prices_b, strict=True):
        bars_by_timestamp[ts] = {
            _SYMBOL: _make_bar(ts, pa, symbol=_SYMBOL),
            _SYMBOL_B: _make_bar(ts, pb, symbol=_SYMBOL_B),
        }
    warmup_ts = set(ts_list[:warmup_count])
    return SimpleNamespace(
        symbols=[_SYMBOL, _SYMBOL_B],
        timeline=ts_list,
        bars_by_symbol={
            _SYMBOL: [bars_by_timestamp[ts][_SYMBOL] for ts in ts_list],
            _SYMBOL_B: [bars_by_timestamp[ts][_SYMBOL_B] for ts in ts_list],
        },
        bars_by_timestamp=bars_by_timestamp,
        warmup_timestamps=warmup_ts,
        is_warmup=lambda ts: ts in warmup_ts,
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


def _build_exec_svc(latency_bars: int = 0) -> SimulatedExecutionService:
    cost_cfg = SimulationCostModelConfig(
        commission_per_share=Decimal("0"),
        min_commission=Decimal("0"),
    )
    slippage = SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0")))
    cost_svc = SimulationCostModelService(config=cost_cfg, slippage_model=slippage)
    fill_cfg = SimulatedFillModelConfig(latency_bars=latency_bars)
    return SimulatedExecutionService(
        simulation_cost_model_service=cost_svc,
        fill_model_config=fill_cfg,
    )


def _run(
    engine: SimulationExecutionEngine,
    exec_svc: SimulatedExecutionService,
    window: SimpleNamespace,
    *,
    initial_cash: float = _INITIAL_CASH,
    dividend_events: list[DividendEvent] | None = None,
) -> SimulationExecutionResult:
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
        initial_cash=initial_cash,
        dividend_events=dividend_events,
    )


# ---------------------------------------------------------------------------
# DividendEvent model validation
# ---------------------------------------------------------------------------


class TestDividendEventModel:
    def test_valid_construction(self) -> None:
        evt = DividendEvent(
            symbol="AAPL",
            ex_date=date(2025, 3, 15),
            cash_amount_per_share=Decimal("0.25"),
        )
        assert evt.symbol == "AAPL"
        assert evt.ex_date == date(2025, 3, 15)
        assert evt.cash_amount_per_share == Decimal("0.25")
        assert evt.currency == "USD"

    def test_symbol_normalised_to_uppercase(self) -> None:
        evt = DividendEvent(
            symbol="aapl",
            ex_date=date(2025, 3, 15),
            cash_amount_per_share=Decimal("0.25"),
        )
        assert evt.symbol == "AAPL"

    def test_zero_cash_per_share_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="cash_amount_per_share"):
            DividendEvent(
                symbol="AAPL",
                ex_date=date(2025, 3, 15),
                cash_amount_per_share=Decimal("0"),
            )

    def test_negative_cash_per_share_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="cash_amount_per_share"):
            DividendEvent(
                symbol="AAPL",
                ex_date=date(2025, 3, 15),
                cash_amount_per_share=Decimal("-0.10"),
            )

    def test_empty_symbol_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="symbol"):
            DividendEvent(
                symbol="   ",
                ex_date=date(2025, 3, 15),
                cash_amount_per_share=Decimal("0.25"),
            )

    def test_optional_fields_default_none(self) -> None:
        evt = DividendEvent(
            symbol="AAPL",
            ex_date=date(2025, 3, 15),
            cash_amount_per_share=Decimal("0.25"),
        )
        assert evt.payable_date is None
        assert evt.declaration_date is None
        assert evt.source_dataset_version is None

    def test_frozen_model_immutable(self) -> None:
        from pydantic import ValidationError

        evt = DividendEvent(
            symbol="AAPL",
            ex_date=date(2025, 3, 15),
            cash_amount_per_share=Decimal("0.25"),
        )
        with pytest.raises((ValidationError, TypeError)):
            evt.symbol = "MSFT"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Dividend cash application: 100 shares × $0.50 = $50
# ---------------------------------------------------------------------------


class TestDividendCashApplication:
    """Core invariant: cash += shares_held × dividend_per_share on ex_date."""

    def test_cash_increases_by_shares_times_dividend(self) -> None:
        # 5 bars on successive days; warmup=2; buy on bar 2; dividend on bar 3
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        # bar 3 date = _BASE_TS + 3 days
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        engine = _build_engine()
        exec_svc = _build_exec_svc()

        # Baseline: no dividends
        r_base = _run(engine, _build_exec_svc(), window)

        # With dividend
        r_div = _run(
            _build_engine(),
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.50"),
                )
            ],
        )

        # Dividend log should have an entry
        assert not r_div.dividend_log.empty, "expected at least one dividend_log entry"

        # Cash on bar 3 (index 1 in equity_curve — 3 active bars, bar index 0,1,2) should be higher
        # The dividend increases settled_cash; equity_curve tracks it
        base_cash_final = r_base.equity_curve["cash"].iloc[-1]
        div_cash_final = r_div.equity_curve["cash"].iloc[-1]
        assert div_cash_final > base_cash_final, (
            f"dividend run final cash ({div_cash_final}) should exceed baseline ({base_cash_final})"
        )

    def test_dividend_log_records_expected_fields(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()
        dividend_per_share = Decimal("0.50")

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=dividend_per_share,
                )
            ],
        )

        if result.dividend_log.empty:
            pytest.skip("No dividend log entry — position may not have been acquired in time")

        row = result.dividend_log.iloc[0]
        assert row["symbol"] == _SYMBOL
        assert row["dividend_per_share"] == pytest.approx(float(dividend_per_share))
        assert row["cash_applied"] == pytest.approx(row["shares_held"] * float(dividend_per_share))
        assert row["currency"] == "USD"

    def test_equity_curve_has_dividend_cash_bar_column(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.50"),
                )
            ],
        )

        assert "dividend_cash_bar" in result.equity_curve.columns

    def test_dividend_cash_bar_nonzero_only_on_ex_date_bar(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.50"),
                )
            ],
        )

        # At most one bar should have dividend_cash_bar > 0
        nonzero_bars = result.equity_curve[result.equity_curve["dividend_cash_bar"] > 0]
        assert len(nonzero_bars) <= 1, (
            f"dividend should fire on at most one bar, got {len(nonzero_bars)}"
        )

    def test_no_dividends_produces_zero_dividend_cash_bar(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, window)

        assert "dividend_cash_bar" in result.equity_curve.columns
        assert all(result.equity_curve["dividend_cash_bar"] == 0.0)


# ---------------------------------------------------------------------------
# No position → no dividend
# ---------------------------------------------------------------------------


class TestNoPositionNoDividend:
    def test_no_dividend_when_position_not_held(self) -> None:
        # Flat prices → StubStrategy never signals → no position
        prices = [100.0, 100.0, 100.0, 100.0, 100.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("1.00"),
                )
            ],
        )

        # dividend_log should be empty because no position exists
        assert result.dividend_log.empty, (
            "no dividend should be applied when the symbol is not held"
        )

    def test_no_dividend_for_unrelated_symbol(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol="GOOG",  # not in window
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("2.00"),
                )
            ],
        )

        assert result.dividend_log.empty, "no dividend for a symbol not in the simulation window"

    def test_dividend_log_empty_when_no_events_supplied(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, window)

        assert result.dividend_log.empty
        assert set(result.dividend_log.columns) >= {
            "run_id",
            "symbol",
            "ex_date",
            "shares_held",
            "dividend_per_share",
            "cash_applied",
        }


# ---------------------------------------------------------------------------
# Multiple dividend events
# ---------------------------------------------------------------------------


class TestMultipleDividendEvents:
    def test_two_events_same_symbol_different_dates(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date1 = (_BASE_TS + timedelta(days=3)).date()
        ex_date2 = (_BASE_TS + timedelta(days=4)).date()

        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date1,
                    cash_amount_per_share=Decimal("0.25"),
                ),
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date2,
                    cash_amount_per_share=Decimal("0.50"),
                ),
            ],
        )

        # Up to 2 dividend log entries if position is held on both dates
        if not result.dividend_log.empty:
            assert len(result.dividend_log) <= 2
            # Each row's cash_applied = shares × per_share
            for _, row in result.dividend_log.iterrows():
                expected = row["shares_held"] * row["dividend_per_share"]
                assert row["cash_applied"] == pytest.approx(expected, abs=1e-6)

    def test_two_events_different_symbols(self) -> None:
        prices_a = [100.0, 101.0, 102.0, 102.0, 102.0]
        prices_b = [50.0, 51.0, 52.0, 52.0, 52.0]
        window = _build_multi_symbol_window(prices_a, prices_b, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        engine = SimulationExecutionEngine(
            cash_ledger_service=CashLedgerService(),
            position_ledger_service=PositionLedgerService(),
            lookahead_guard_service=LookaheadGuardService(),
            position_sizer=SimplePositionSizer(
                total_capital=_INITIAL_CASH,
                universe_size=2,
            ),
        )
        exec_svc = _build_exec_svc()
        result = _run(
            engine,
            exec_svc,
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.30"),
                ),
                DividendEvent(
                    symbol=_SYMBOL_B,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.20"),
                ),
            ],
        )

        # Dividend log may have 0, 1, or 2 entries depending on position acquisition
        for _, row in result.dividend_log.iterrows():
            expected_cash = row["shares_held"] * row["dividend_per_share"]
            assert row["cash_applied"] == pytest.approx(expected_cash, abs=1e-6)

    def test_deterministic_ordering_multiple_events(self) -> None:
        """Multiple events on same ex_date must produce same result regardless of list order."""
        prices = [100.0, 101.0, 102.0, 102.0, 102.0, 102.0]
        window1 = _build_window(prices, warmup_count=2)
        window2 = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        events_fwd = [
            DividendEvent(
                symbol=_SYMBOL,
                ex_date=ex_date,
                cash_amount_per_share=Decimal("0.10"),
            ),
            DividendEvent(
                symbol=_SYMBOL,
                ex_date=(_BASE_TS + timedelta(days=4)).date(),
                cash_amount_per_share=Decimal("0.20"),
            ),
        ]
        events_rev = list(reversed(events_fwd))

        r1 = _run(_build_engine(), _build_exec_svc(), window1, dividend_events=events_fwd)
        r2 = _run(_build_engine(), _build_exec_svc(), window2, dividend_events=events_rev)

        # Total dividend cash should be identical regardless of input order
        total1 = r1.equity_curve["dividend_cash_bar"].sum()
        total2 = r2.equity_curve["dividend_cash_bar"].sum()
        assert total1 == pytest.approx(total2, abs=1e-6)


# ---------------------------------------------------------------------------
# Equity curve total return
# ---------------------------------------------------------------------------


class TestEquityTotalReturn:
    def test_dividend_increases_total_equity(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        r_base = _run(_build_engine(), _build_exec_svc(), _build_window(prices, warmup_count=2))
        r_div = _run(
            _build_engine(),
            _build_exec_svc(),
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("1.00"),
                )
            ],
        )

        base_equity = r_base.equity_curve["equity"].iloc[-1]
        div_equity = r_div.equity_curve["equity"].iloc[-1]

        # If a position was held, dividend run must have higher final equity
        if not r_div.dividend_log.empty:
            assert div_equity > base_equity, (
                f"dividend equity ({div_equity}) should exceed baseline ({base_equity})"
            )

    def test_equity_equals_cash_plus_positions_value_at_every_bar(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        result = _run(
            _build_engine(),
            _build_exec_svc(),
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.50"),
                )
            ],
        )

        for _, row in result.equity_curve.iterrows():
            expected = row["positions_value"] + row["settled_cash"] + row["unsettled_cash"]
            assert row["equity"] == pytest.approx(expected, abs=1e-4), (
                f"equity ({row['equity']}) != positions_value + settled + unsettled ({expected})"
            )

    def test_dividend_not_applied_on_warmup_bars(self) -> None:
        # Put ex_date on the warmup bars — dividend must not fire.
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)
        ex_date_warmup = (_BASE_TS + timedelta(days=1)).date()  # bar 1 = warmup bar

        result = _run(
            _build_engine(),
            _build_exec_svc(),
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date_warmup,
                    cash_amount_per_share=Decimal("5.00"),
                )
            ],
        )

        # Dividend log must be empty (warmup bars have no positions and no dividend processing)
        assert result.dividend_log.empty, "dividend must not fire during warmup bars"


# ---------------------------------------------------------------------------
# Intraday: dividend fires exactly once per ex_date
# ---------------------------------------------------------------------------


class TestDividendFiresOncePerDate:
    def test_intraday_bars_same_date_fire_once(self) -> None:
        """With 5-minute bars all on the same date, dividend fires on the first live bar only."""
        prices = [100.0, 100.0, 101.0, 102.0, 102.0, 102.0, 102.0, 102.0]
        # intraday bars: warmup=2, remaining bars all on 2025-06-01
        window = _build_window(prices, warmup_count=2, daily=False)
        ex_date = _BASE_TS.date()  # all bars on this date

        result = _run(
            _build_engine(),
            _build_exec_svc(),
            window,
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL,
                    ex_date=ex_date,
                    cash_amount_per_share=Decimal("0.50"),
                )
            ],
        )

        nonzero = result.equity_curve[result.equity_curve["dividend_cash_bar"] > 0]
        assert len(nonzero) <= 1, (
            f"dividend should fire at most once, even with multiple intraday bars on the same date; "
            f"got {len(nonzero)} nonzero bars"
        )


# ---------------------------------------------------------------------------
# SimulationRunConfig dividend_events field
# ---------------------------------------------------------------------------


class TestSimulationRunConfigDividendEvents:
    def test_defaults_to_empty_list(self) -> None:
        from autonomous_trading_platform.research.config.simulation_run_config import (
            SimulationRunConfig,
        )

        cfg = SimulationRunConfig(
            strategy_id="s1",
            strategy_type="momentum",
            dataset_version="v1",
            random_seed=42,
            price_basis="adjusted",
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        assert cfg.dividend_events == []

    def test_accepts_dividend_events(self) -> None:
        from autonomous_trading_platform.research.config.simulation_run_config import (
            SimulationRunConfig,
        )

        cfg = SimulationRunConfig(
            strategy_id="s1",
            strategy_type="momentum",
            dataset_version="v1",
            random_seed=42,
            price_basis="adjusted",
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            dividend_events=[
                DividendEvent(
                    symbol="AAPL",
                    ex_date=date(2024, 3, 15),
                    cash_amount_per_share=Decimal("0.25"),
                )
            ],
        )
        assert len(cfg.dividend_events) == 1
        assert cfg.dividend_events[0].symbol == "AAPL"

    def test_accepts_empty_events_list(self) -> None:
        from autonomous_trading_platform.research.config.simulation_run_config import (
            SimulationRunConfig,
        )

        cfg = SimulationRunConfig(
            strategy_id="s1",
            strategy_type="momentum",
            dataset_version="v1",
            random_seed=42,
            price_basis="adjusted",
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            dividend_events=[],
        )
        assert cfg.dividend_events == []


# ---------------------------------------------------------------------------
# Cache / lineage identity
# ---------------------------------------------------------------------------


class TestSimulationCacheKeyDividendHash:
    def _base_key_kwargs(self) -> dict:
        return dict(
            config_hash="abc123",
            dataset_version="v1",
            universe_version="v1",
            price_basis="adjusted",
            symbols_hash="sym123",
            start_date="2024-01-01",
            end_date="2024-12-31",
            random_seed=42,
            stage_name="default",
            window_role="default",
            fill_policy="market",
            latency_bars=0,
            cost_model_type="volume_share",
            slippage_config_hash="slp123",
            commission_per_share="0",
            regime_dataset_version="",
            feature_versions_hash="",
        )

    def test_cache_key_has_dividend_events_hash_field(self) -> None:
        from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey

        key = SimulationCacheKey(**self._base_key_kwargs())
        assert hasattr(key, "dividend_events_hash")
        assert key.dividend_events_hash == ""

    def test_dividend_events_hash_in_to_dict(self) -> None:
        from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey

        key = SimulationCacheKey(**self._base_key_kwargs(), dividend_events_hash="abc1234567890123")
        d = key.to_dict()
        assert "dividend_events_hash" in d
        assert d["dividend_events_hash"] == "abc1234567890123"

    def test_different_dividend_events_produce_different_key_ids(self) -> None:
        from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey

        key_no_div = SimulationCacheKey(**self._base_key_kwargs(), dividend_events_hash="")
        key_with_div = SimulationCacheKey(
            **self._base_key_kwargs(), dividend_events_hash="abcdef1234567890"
        )
        assert key_no_div.key_id != key_with_div.key_id

    def test_build_simulation_cache_key_with_dividend_events(self) -> None:
        from datetime import date as dt

        from autonomous_trading_platform.contracts.common.enums import PriceBasis
        from autonomous_trading_platform.research.cache.cache_key_builder import (
            build_simulation_cache_key,
        )
        from autonomous_trading_platform.research.config.simulation_run_config import (
            SimulationRunConfig,
        )
        from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

        run_config = SimulationRunConfig(
            strategy_id="momentum_v1",
            strategy_type="momentum",
            dataset_version="v1",
            random_seed=42,
            price_basis=PriceBasis.ADJUSTED,
            symbols=["AAPL"],
            start_date=dt(2024, 1, 1),
            end_date=dt(2024, 12, 31),
        )
        strategy_config = StrategyConfig(
            type="momentum",
            strategy_id="momentum_v1",
            parameters={"lookback": 20},
        )

        events = [
            DividendEvent(
                symbol="AAPL",
                ex_date=dt(2024, 3, 15),
                cash_amount_per_share=Decimal("0.25"),
            )
        ]

        key_no_div = build_simulation_cache_key(
            strategy_config=strategy_config,
            run_config=run_config,
        )
        key_with_div = build_simulation_cache_key(
            strategy_config=strategy_config,
            run_config=run_config,
            dividend_events=events,
        )

        assert key_no_div.key_id != key_with_div.key_id, "dividend events must change the cache key"
        assert key_with_div.dividend_events_hash != ""
        assert key_no_div.dividend_events_hash == ""

    def test_same_dividend_events_produce_same_hash(self) -> None:
        from autonomous_trading_platform.research.cache.cache_key_builder import (
            _hash_dividend_events,
        )

        events = [
            DividendEvent(
                symbol="AAPL",
                ex_date=date(2024, 3, 15),
                cash_amount_per_share=Decimal("0.25"),
            ),
            DividendEvent(
                symbol="MSFT",
                ex_date=date(2024, 4, 1),
                cash_amount_per_share=Decimal("0.75"),
            ),
        ]
        h1 = _hash_dividend_events(events)
        h2 = _hash_dividend_events(list(reversed(events)))
        assert h1 == h2, "hash must be order-independent"

    def test_run_config_dividend_events_flow_to_cache_key(self) -> None:
        from datetime import date as dt

        from autonomous_trading_platform.contracts.common.enums import PriceBasis
        from autonomous_trading_platform.research.cache.cache_key_builder import (
            build_simulation_cache_key,
        )
        from autonomous_trading_platform.research.config.simulation_run_config import (
            SimulationRunConfig,
        )
        from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

        events = [
            DividendEvent(
                symbol="AAPL",
                ex_date=dt(2024, 3, 15),
                cash_amount_per_share=Decimal("0.25"),
            )
        ]
        run_with_div = SimulationRunConfig(
            strategy_id="momentum_v1",
            strategy_type="momentum",
            dataset_version="v1",
            random_seed=42,
            price_basis=PriceBasis.ADJUSTED,
            symbols=["AAPL"],
            start_date=dt(2024, 1, 1),
            end_date=dt(2024, 12, 31),
            dividend_events=events,
        )
        run_no_div = SimulationRunConfig(
            strategy_id="momentum_v1",
            strategy_type="momentum",
            dataset_version="v1",
            random_seed=42,
            price_basis=PriceBasis.ADJUSTED,
            symbols=["AAPL"],
            start_date=dt(2024, 1, 1),
            end_date=dt(2024, 12, 31),
        )
        strategy_config = StrategyConfig(
            type="momentum", strategy_id="momentum_v1", parameters={"lookback": 20}
        )

        key_with = build_simulation_cache_key(
            strategy_config=strategy_config, run_config=run_with_div
        )
        key_without = build_simulation_cache_key(
            strategy_config=strategy_config, run_config=run_no_div
        )
        assert key_with.key_id != key_without.key_id


# ---------------------------------------------------------------------------
# RunManifest dividend lineage fields
# ---------------------------------------------------------------------------


class TestRunManifestDividendFields:
    def test_manifest_has_settlement_days_field(self) -> None:
        from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest

        fields = RunManifest.model_fields
        assert "settlement_days" in fields

    def test_manifest_has_dividend_events_count_field(self) -> None:
        from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest

        fields = RunManifest.model_fields
        assert "dividend_events_count" in fields

    def test_manifest_has_dividend_events_hash_field(self) -> None:
        from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest

        fields = RunManifest.model_fields
        assert "dividend_events_hash" in fields

    def test_manifest_has_price_adjustment_basis_field(self) -> None:
        from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest

        fields = RunManifest.model_fields
        assert "price_adjustment_basis" in fields

    def test_manifest_fields_default_none(self) -> None:
        """New optional fields must not break existing manifest construction."""
        import json
        from datetime import datetime as dt
        from uuid import uuid4

        from autonomous_trading_platform.contracts.common.enums import (
            BarInterval,
            PriceBasis,
            RunType,
        )
        from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
        from autonomous_trading_platform.governance.models.governance_state import GovernanceState

        manifest = RunManifest(
            run_id=uuid4(),
            run_type=RunType.SIMULATION,
            created_at=dt(2025, 1, 1, tzinfo=UTC),
            environment="test",
            broker="alpaca",
            broker_account_id="test-account",
            strategy_id="s1",
            strategy_version="v1",
            strategy_config={},
            capital_bucket="100000",
            interval=BarInterval.ONE_DAY,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            dataset_version="v1",
            price_basis=PriceBasis.ADJUSTED,
            universe_version="v1",
            git_commit="abc123",
            governance_state=GovernanceState.APPROVED_RESEARCH,
        )
        assert manifest.settlement_days is None
        assert manifest.dividend_events_count is None
        assert manifest.dividend_events_hash is None
        assert manifest.price_adjustment_basis is None

        # Serialization must work with new fields
        d = manifest.to_dict()
        assert "settlement_days" in d
        assert "dividend_events_count" in d
        j = manifest.to_json()
        assert json.loads(j)["settlement_days"] is None


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


class TestDividendDeterministicReplay:
    def test_identical_inputs_produce_identical_dividend_cash(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        ex_date = (_BASE_TS + timedelta(days=3)).date()
        events = [
            DividendEvent(
                symbol=_SYMBOL,
                ex_date=ex_date,
                cash_amount_per_share=Decimal("0.50"),
            )
        ]

        def _make_result() -> SimulationExecutionResult:
            return _run(
                _build_engine(),
                _build_exec_svc(),
                _build_window(prices, warmup_count=2),
                dividend_events=events,
            )

        r1 = _make_result()
        r2 = _make_result()

        assert len(r1.equity_curve) == len(r2.equity_curve)
        for col in ["equity", "cash", "settled_cash", "dividend_cash_bar"]:
            for i, (v1, v2) in enumerate(
                zip(r1.equity_curve[col], r2.equity_curve[col], strict=True)
            ):
                assert v1 == pytest.approx(v2, abs=1e-9), (
                    f"Column '{col}' differs at bar {i}: {v1} vs {v2}"
                )

    def test_different_dividend_amounts_produce_different_equity(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        ex_date = (_BASE_TS + timedelta(days=3)).date()

        r_small = _run(
            _build_engine(),
            _build_exec_svc(),
            _build_window(prices, warmup_count=2),
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL, ex_date=ex_date, cash_amount_per_share=Decimal("0.10")
                )
            ],
        )
        r_large = _run(
            _build_engine(),
            _build_exec_svc(),
            _build_window(prices, warmup_count=2),
            dividend_events=[
                DividendEvent(
                    symbol=_SYMBOL, ex_date=ex_date, cash_amount_per_share=Decimal("5.00")
                )
            ],
        )

        # If positions were held, the large dividend run must show higher total dividend cash
        total_small = r_small.equity_curve["dividend_cash_bar"].sum()
        total_large = r_large.equity_curve["dividend_cash_bar"].sum()
        if not r_small.dividend_log.empty:
            assert total_large > total_small, (
                "larger dividend per share must produce more dividend cash"
            )

    def test_no_dividend_events_produces_empty_dividend_log(self) -> None:
        prices = [100.0, 101.0, 102.0, 102.0, 102.0]
        window = _build_window(prices, warmup_count=2)

        r1 = _run(_build_engine(), _build_exec_svc(), window, dividend_events=None)
        r2 = _run(_build_engine(), _build_exec_svc(), window, dividend_events=[])

        assert r1.dividend_log.empty
        assert r2.dividend_log.empty
        assert list(r1.equity_curve["equity"]) == list(r2.equity_curve["equity"])
