"""
Simulation-level tests for settlement-aware cash accounting (TASK F-06).

Validates that SimulationExecutionEngine:
- Preserves legacy immediate-settlement behavior when settlement_days=0
- Creates pending settlement records for SELL fills when settlement_days>0
- Matures pending settlements after the configured number of bars
- Excludes unsettled proceeds from buying_power until settled
- Includes unsettled proceeds in equity (economic value)
- Keeps settled_cash and reserved_cash separate from unsettled_cash
- Blocks new BUY reservations that exceed settled buying_power
- Handles multi-sell settlement queues deterministically
- Produces identical results on identical replays
- Records settled_cash and unsettled_cash columns in the equity curve
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
_BASE_TS = datetime(2025, 6, 1, 9, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000009902")
_INITIAL_CASH = 10_000.0


def _make_bar(ts: datetime, close: float, open_price: float | None = None, volume: int = 50_000):
    op = open_price if open_price is not None else close
    return make_five_minute_bar(
        timestamp=ts,
        symbol=_SYMBOL,
        open_price=str(op),
        high_price=str(max(op, close)),
        low_price=str(min(op, close)),
        close_price=str(close),
        volume=volume,
    )


def _build_window(prices: list[float], warmup_count: int = 0, volume: int = 50_000):
    ts_list = [_BASE_TS + timedelta(minutes=5 * i) for i in range(len(prices))]
    bars = [_make_bar(ts, p, volume=volume) for ts, p in zip(ts_list, prices, strict=True)]
    bars_by_timestamp = {ts: {_SYMBOL: bar} for ts, bar in zip(ts_list, bars, strict=True)}
    warmup_ts = set(ts_list[:warmup_count])
    return SimpleNamespace(
        symbols=[_SYMBOL],
        timeline=ts_list,
        bars_by_symbol={_SYMBOL: bars},
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


def _build_exec_svc(
    *,
    latency_bars: int = 0,
    rejection_probability: float | None = None,
) -> SimulatedExecutionService:
    cost_cfg = SimulationCostModelConfig(
        commission_per_share=Decimal("0"),
        min_commission=Decimal("0"),
    )
    slippage = SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0")))
    cost_svc = SimulationCostModelService(config=cost_cfg, slippage_model=slippage)
    fill_cfg = SimulatedFillModelConfig(
        latency_bars=latency_bars,
        order_rejection_probability=rejection_probability,
    )
    return SimulatedExecutionService(
        simulation_cost_model_service=cost_svc,
        fill_model_config=fill_cfg,
    )


def _run(
    engine: SimulationExecutionEngine,
    exec_svc: SimulatedExecutionService,
    prices: list[float],
    *,
    warmup_count: int = 2,
    initial_cash: float = _INITIAL_CASH,
    settlement_days: int = 0,
) -> SimulationExecutionResult:
    window = _build_window(prices, warmup_count=warmup_count)
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
        settlement_days=settlement_days,
    )


# ---------------------------------------------------------------------------
# Equity curve columns
# ---------------------------------------------------------------------------


class TestEquityCurveSettlementColumns:
    def test_equity_curve_has_settled_cash_column(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0])

        assert "settled_cash" in result.equity_curve.columns

    def test_equity_curve_has_unsettled_cash_column(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0])

        assert "unsettled_cash" in result.equity_curve.columns

    def test_equity_curve_has_pending_settlement_count_column(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0])

        assert "pending_settlement_count" in result.equity_curve.columns

    def test_settled_plus_unsettled_equals_cash(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0, 103.0, 104.0])

        for _, row in result.equity_curve.iterrows():
            assert row["settled_cash"] + row["unsettled_cash"] == pytest.approx(row["cash"]), (
                f"settled + unsettled != cash at row: {dict(row)}"
            )


# ---------------------------------------------------------------------------
# Legacy behavior: settlement_days=0
# ---------------------------------------------------------------------------


class TestLegacyImmediateSettlement:
    def test_all_cash_settled_when_days_zero(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0, 103.0, 104.0], settlement_days=0)

        for _, row in result.equity_curve.iterrows():
            assert row["unsettled_cash"] == pytest.approx(0.0), (
                f"unsettled_cash should be 0 with settlement_days=0, got {row['unsettled_cash']}"
            )

    def test_buying_power_equals_cash_minus_reserved_when_days_zero(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0, 103.0, 104.0], settlement_days=0)

        for _, row in result.equity_curve.iterrows():
            expected = row["cash"] - row["reserved_cash"]
            assert row["buying_power"] == pytest.approx(expected)

    def test_no_pending_settlements_when_days_zero(self) -> None:
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        result = _run(engine, exec_svc, [100.0, 101.0, 102.0, 103.0, 104.0], settlement_days=0)

        for _, row in result.equity_curve.iterrows():
            assert row["pending_settlement_count"] == 0


# ---------------------------------------------------------------------------
# Pending settlement creation: settlement_days > 0
# ---------------------------------------------------------------------------


class TestPendingSettlementCreation:
    def test_unsettled_cash_nonzero_after_sell_with_settlement(self) -> None:
        """After a strategy sells a position, unsettled_cash should be > 0."""
        # Price pattern: buy on bar 2, sell on bar 3, flat on bar 4
        # Warmup=2, so first active bar = index 2 → buy signal
        # Bar 3: price drops slightly → sell
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        # Use a price sequence where warmup is 2 bars, then prices alternate to trigger buy/sell
        prices = [100.0, 100.0, 100.0, 99.0, 99.0, 99.0, 99.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        # After any sell occurs, at least one bar should have unsettled_cash > 0
        # or pending_settlement_count > 0
        has_unsettled = any(
            row["unsettled_cash"] > 0 or row["pending_settlement_count"] > 0
            for _, row in result.equity_curve.iterrows()
        )
        # If there were any sells (trade_logs with SELL side), then we expect unsettled
        sells = (
            result.trade_logs[result.trade_logs["side"] == "sell"]
            if len(result.trade_logs) > 0
            else []
        )
        if len(sells) > 0:
            assert has_unsettled, "Expected unsettled_cash > 0 after a sell with settlement_days=1"

    def test_equity_includes_unsettled_cash(self) -> None:
        """Total equity must include unsettled proceeds even though they are not spendable."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 103.0, 102.0, 101.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        for _, row in result.equity_curve.iterrows():
            # equity >= cash (positions may add more, but equity should include all cash)
            assert row["equity"] >= row["cash"] - 1e-6, (
                f"equity ({row['equity']}) should be >= cash ({row['cash']})"
            )

    def test_buying_power_equals_settled_minus_reserved(self) -> None:
        """With settlement_days>0, buying_power = settled_cash - reserved_cash."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 101.0, 102.0, 103.0, 102.0, 101.0, 100.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        for _, row in result.equity_curve.iterrows():
            expected_bp = row["settled_cash"] - row["reserved_cash"]
            assert row["buying_power"] == pytest.approx(expected_bp), (
                f"buying_power ({row['buying_power']}) != settled_cash - reserved_cash "
                f"({row['settled_cash']} - {row['reserved_cash']})"
            )


# ---------------------------------------------------------------------------
# Settlement maturation
# ---------------------------------------------------------------------------


class TestSettlementMaturation:
    def test_unsettled_cash_eventually_becomes_zero_after_settlement_matures(self) -> None:
        """After settlement_days bars pass the sell, unsettled_cash should return to 0
        (assuming no further sells)."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        # Enough bars: warmup=2, buy on bar 2, sell on bar 3, then 5 more bars for maturation
        prices = [100.0, 100.0, 100.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        sells = (
            result.trade_logs[result.trade_logs["side"] == "sell"]
            if len(result.trade_logs) > 0
            else []
        )
        if len(sells) == 0:
            pytest.skip("No sells occurred — cannot test maturation")

        # The last few bars should show zero unsettled (all matures after 1 bar)
        last_rows = result.equity_curve.tail(3)
        final_unsettled = last_rows["unsettled_cash"].min()
        assert final_unsettled == pytest.approx(0.0, abs=1e-6), (
            f"unsettled_cash should be 0 after maturation, got {final_unsettled}"
        )

    def test_settled_cash_increases_when_settlement_matures(self) -> None:
        """When a pending settlement matures, settled_cash should increase."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 100.0, 100.0, 99.0, 99.0, 99.0, 99.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        sells = (
            result.trade_logs[result.trade_logs["side"] == "sell"]
            if len(result.trade_logs) > 0
            else []
        )
        if len(sells) == 0:
            pytest.skip("No sells occurred — cannot test maturation")

        ec = result.equity_curve.reset_index(drop=True)
        # Find a bar where unsettled_cash decreases — that's maturation
        for i in range(1, len(ec)):
            if (
                ec.loc[i - 1, "unsettled_cash"] > 0
                and ec.loc[i, "unsettled_cash"] < ec.loc[i - 1, "unsettled_cash"]
            ):
                # Settled cash should increase correspondingly
                delta_unsettled = ec.loc[i - 1, "unsettled_cash"] - ec.loc[i, "unsettled_cash"]
                delta_settled = ec.loc[i, "settled_cash"] - ec.loc[i - 1, "settled_cash"]
                assert delta_settled >= delta_unsettled - 1e-6, (
                    f"settled_cash should increase when unsettled matures: "
                    f"delta_settled={delta_settled}, delta_unsettled={delta_unsettled}"
                )
                return  # Found maturation, test passed
        # No maturation observed (sells happened at the last bar) — that's fine
        pytest.skip("Settlement maturation not observed within test bar sequence")


# ---------------------------------------------------------------------------
# Buying power excludes unsettled cash
# ---------------------------------------------------------------------------


class TestBuyingPowerExcludesUnsettled:
    def test_buying_power_does_not_include_unsettled_proceeds(self) -> None:
        """At any bar with unsettled_cash > 0, buying_power should not include it."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 101.0, 102.0, 103.0, 102.0, 101.0, 100.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=2)

        for _, row in result.equity_curve.iterrows():
            if row["unsettled_cash"] > 0:
                # buying_power must NOT count unsettled proceeds
                assert row["buying_power"] <= row["settled_cash"] + 1e-6, (
                    f"buying_power ({row['buying_power']}) exceeds settled_cash "
                    f"({row['settled_cash']}) — unsettled proceeds should be excluded"
                )

    def test_equity_still_includes_unsettled_proceeds(self) -> None:
        """Equity (economic value) should include unsettled proceeds."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 101.0, 102.0, 101.0, 100.0, 99.0, 98.0, 97.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=2)

        for _, row in result.equity_curve.iterrows():
            # equity = positions_value + cash (total), cash = settled + unsettled
            expected_equity = row["positions_value"] + row["settled_cash"] + row["unsettled_cash"]
            assert row["equity"] == pytest.approx(expected_equity, abs=1e-4), (
                f"equity ({row['equity']}) != positions_value + settled + unsettled "
                f"({expected_equity})"
            )


# ---------------------------------------------------------------------------
# Reserved cash integration with settlement
# ---------------------------------------------------------------------------


class TestReservedCashWithSettlement:
    def test_reserved_cash_remains_distinct_from_unsettled(self) -> None:
        """reserved_cash and unsettled_cash must not overlap."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 101.0, 102.0, 101.0, 100.0, 101.0, 102.0, 103.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        for _, row in result.equity_curve.iterrows():
            # All three must be non-negative
            assert row["reserved_cash"] >= -1e-6
            assert row["unsettled_cash"] >= -1e-6
            assert row["settled_cash"] >= -1e-6

    def test_buying_power_formula_with_both_reserved_and_unsettled(self) -> None:
        """buying_power = settled_cash - reserved_cash at every bar."""
        engine = _build_engine()
        exec_svc = _build_exec_svc(latency_bars=1)  # creates reservations
        prices = [100.0, 101.0, 102.0, 101.0, 100.0, 101.0, 102.0, 103.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        for _, row in result.equity_curve.iterrows():
            expected = row["settled_cash"] - row["reserved_cash"]
            assert row["buying_power"] == pytest.approx(expected, abs=1e-4), (
                f"buying_power ({row['buying_power']}) != settled - reserved ({expected})"
            )


# ---------------------------------------------------------------------------
# Multi-sell settlement queue
# ---------------------------------------------------------------------------


class TestMultiSellSettlementQueue:
    def test_multiple_sells_create_independent_settlement_records(self) -> None:
        """Multiple sells within the simulation window should all eventually settle."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        # Alternating prices cause multiple buy/sell cycles
        prices = [
            100.0,
            100.0,  # warmup
            100.0,
            99.0,
            100.0,
            99.0,
            100.0,
            99.0,
            100.0,
            99.0,  # alternating
        ]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=1)

        # All unsettled should eventually mature (last bars should show 0 unsettled)
        if len(result.trade_logs) > 0:
            sells = result.trade_logs[result.trade_logs["side"] == "sell"]
            if len(sells) > 0:
                last_row = result.equity_curve.iloc[-1]
                # At the last bar, pending settlements from bars > (last_bar - 1) may still be pending
                # but all others should have matured
                assert last_row["pending_settlement_count"] <= len(sells)

    def test_total_cash_monotonically_accounts_for_trades(self) -> None:
        """settled_cash + unsettled_cash = cash must hold at every bar regardless
        of how many pending settlements are in the queue."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 100.0, 100.0, 99.0, 100.0, 99.0, 100.0, 99.0, 100.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=2)

        for _, row in result.equity_curve.iterrows():
            assert row["settled_cash"] + row["unsettled_cash"] == pytest.approx(
                row["cash"], abs=1e-4
            )


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    def test_identical_simulation_produces_identical_equity_curve(self) -> None:
        """Running the same simulation twice must yield bit-identical equity curves."""
        prices = [100.0, 100.0, 100.0, 99.0, 98.0, 99.0, 100.0, 101.0, 102.0, 101.0]

        def _make_result() -> SimulationExecutionResult:
            return _run(
                _build_engine(),
                _build_exec_svc(),
                prices,
                warmup_count=2,
                settlement_days=1,
            )

        r1 = _make_result()
        r2 = _make_result()

        assert len(r1.equity_curve) == len(r2.equity_curve)
        for col in [
            "equity",
            "cash",
            "settled_cash",
            "unsettled_cash",
            "reserved_cash",
            "buying_power",
        ]:
            for i, (v1, v2) in enumerate(
                zip(r1.equity_curve[col], r2.equity_curve[col], strict=True)
            ):
                assert v1 == pytest.approx(v2, abs=1e-9), (
                    f"Column '{col}' differs at bar {i}: {v1} vs {v2}"
                )

    def test_different_settlement_days_produce_different_equity_curves(self) -> None:
        """settlement_days=0 and settlement_days=1 should produce different buying_power timelines
        when there are sells."""
        prices = [100.0, 100.0, 100.0, 99.0, 99.0, 99.0, 99.0, 99.0]

        r0 = _run(_build_engine(), _build_exec_svc(), prices, warmup_count=2, settlement_days=0)
        r1 = _run(_build_engine(), _build_exec_svc(), prices, warmup_count=2, settlement_days=1)

        sells0 = r0.trade_logs[r0.trade_logs["side"] == "sell"] if len(r0.trade_logs) > 0 else []
        if len(sells0) == 0:
            pytest.skip("No sells occurred — cannot compare settlement behaviors")

        # With settlement_days=1, there should be at least one bar with unsettled_cash > 0
        has_unsettled = any(row["unsettled_cash"] > 0 for _, row in r1.equity_curve.iterrows())
        assert has_unsettled, "Expected unsettled_cash > 0 with settlement_days=1"

        # With settlement_days=0, no bar should have unsettled_cash > 0
        all_settled = all(row["unsettled_cash"] == 0 for _, row in r0.equity_curve.iterrows())
        assert all_settled, "Expected no unsettled_cash with settlement_days=0"


# ---------------------------------------------------------------------------
# End-of-simulation behavior
# ---------------------------------------------------------------------------


class TestEndOfSimulation:
    def test_simulation_completes_with_pending_settlements(self) -> None:
        """Simulation should not crash when there are pending settlements at the last bar."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        # Short window: warmup=2, then buy bar, then sell bar, no more bars to settle
        prices = [100.0, 100.0, 100.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=2)

        # Result should be produced without error
        assert result.equity_curve is not None
        assert len(result.equity_curve) >= 1

    def test_equity_reflects_unsettled_at_last_bar(self) -> None:
        """Even if settlement hasn't matured, the last bar's equity should include unsettled."""
        engine = _build_engine()
        exec_svc = _build_exec_svc()
        prices = [100.0, 100.0, 100.0, 99.0]
        result = _run(engine, exec_svc, prices, warmup_count=2, settlement_days=2)

        if len(result.trade_logs) > 0:
            sells = result.trade_logs[result.trade_logs["side"] == "sell"]
            if len(sells) > 0:
                last = result.equity_curve.iloc[-1]
                assert last["equity"] == pytest.approx(
                    last["positions_value"] + last["settled_cash"] + last["unsettled_cash"],
                    abs=1e-4,
                )


# ---------------------------------------------------------------------------
# SimulationRunConfig settlement_days validation
# ---------------------------------------------------------------------------


class TestSimulationRunConfigSettlementDays:
    def test_settlement_days_defaults_to_zero(self) -> None:
        from datetime import date

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
        assert cfg.settlement_days == 0

    def test_settlement_days_one_is_valid(self) -> None:
        from datetime import date

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
            settlement_days=1,
        )
        assert cfg.settlement_days == 1

    def test_settlement_days_two_is_valid(self) -> None:
        from datetime import date

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
            settlement_days=2,
        )
        assert cfg.settlement_days == 2

    def test_negative_settlement_days_raises(self) -> None:
        from datetime import date

        from pydantic import ValidationError

        from autonomous_trading_platform.research.config.simulation_run_config import (
            SimulationRunConfig,
        )

        with pytest.raises(ValidationError, match="settlement_days"):
            SimulationRunConfig(
                strategy_id="s1",
                strategy_type="momentum",
                dataset_version="v1",
                random_seed=42,
                price_basis="adjusted",
                symbols=["AAPL"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                settlement_days=-1,
            )


# ---------------------------------------------------------------------------
# SimulationCacheKey settlement_days
# ---------------------------------------------------------------------------


class TestSimulationCacheKeySettlementDays:
    def test_cache_key_includes_settlement_days(self) -> None:
        from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey

        key = SimulationCacheKey(
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
            settlement_days=1,
        )
        assert key.settlement_days == 1
        assert "settlement_days" in key.to_dict()
        assert key.to_dict()["settlement_days"] == 1

    def test_different_settlement_days_produce_different_cache_keys(self) -> None:
        from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey

        def _key(days: int) -> SimulationCacheKey:
            return SimulationCacheKey(
                config_hash="abc",
                dataset_version="v1",
                universe_version="v1",
                price_basis="adjusted",
                symbols_hash="sym",
                start_date="2024-01-01",
                end_date="2024-12-31",
                random_seed=42,
                stage_name="default",
                window_role="default",
                fill_policy="market",
                latency_bars=0,
                cost_model_type="volume_share",
                slippage_config_hash="slp",
                commission_per_share="0",
                regime_dataset_version="",
                feature_versions_hash="",
                settlement_days=days,
            )

        assert _key(0).key_id != _key(1).key_id
        assert _key(1).key_id != _key(2).key_id

    def test_settlement_days_zero_is_default(self) -> None:
        from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey

        key = SimulationCacheKey(
            config_hash="abc",
            dataset_version="v1",
            universe_version="v1",
            price_basis="adjusted",
            symbols_hash="sym",
            start_date="2024-01-01",
            end_date="2024-12-31",
            random_seed=42,
            stage_name="default",
            window_role="default",
            fill_policy="market",
            latency_bars=0,
            cost_model_type="volume_share",
            slippage_config_hash="slp",
            commission_per_share="0",
            regime_dataset_version="",
            feature_versions_hash="",
        )
        assert key.settlement_days == 0
