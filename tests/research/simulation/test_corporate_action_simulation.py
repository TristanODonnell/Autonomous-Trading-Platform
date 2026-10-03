"""Research engine corporate actions (plan 5d-E): a split during a hold is applied to
the held position through the shared accounting rule and the strategy's history is
split-adjusted on read, so equity stays continuous across the ex-date; a fractional
remainder is paid in cash at the ex-date price; a split with no position is a no-op."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from autonomous_trading_platform.accounting.corporate_actions import StaticSplitSource
from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
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

_SYMBOL = "NVDA"
_BASE_TS = datetime(2024, 6, 5, 20, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000005d0e")
_INITIAL_CASH = 10_000.0


def _bar(ts: datetime, close: float):
    return make_five_minute_bar(
        timestamp=ts,
        symbol=_SYMBOL,
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        close_price=str(close),
        volume=1_000_000,
    )


def _window(prices: list[float], *, warmup_count: int = 2) -> SimpleNamespace:
    """Daily-spaced bars so each bar is its own calendar date."""
    stamps = [_BASE_TS + timedelta(days=i) for i in range(len(prices))]
    bars = [_bar(ts, p) for ts, p in zip(stamps, prices, strict=True)]
    warmup = set(stamps[:warmup_count])
    return SimpleNamespace(
        symbols=[_SYMBOL],
        timeline=stamps,
        bars_by_symbol={_SYMBOL: bars},
        bars_by_timestamp={ts: {_SYMBOL: b} for ts, b in zip(stamps, bars, strict=True)},
        warmup_timestamps=warmup,
        is_warmup=lambda ts: ts in warmup,
    )


def _split(ratio: str, ex_date: date) -> CorporateAction:
    return CorporateAction(
        action_id=f"nvda-split-{ex_date.isoformat()}",
        symbol=_SYMBOL,
        action_type=(
            CorporateActionType.SPLIT_FORWARD
            if Decimal(ratio) > 1
            else CorporateActionType.SPLIT_REVERSE
        ),
        effective_date=ex_date,
        split_ratio=Decimal(ratio),
        cash_amount=None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


def _engine() -> SimulationExecutionEngine:
    return SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(total_capital=_INITIAL_CASH, universe_size=1),
    )


def _execution_service() -> SimulatedExecutionService:
    cost = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"), min_commission=Decimal("0")
        ),
        slippage_model=SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0"))),
    )
    return SimulatedExecutionService(
        simulation_cost_model_service=cost,
        fill_model_config=SimulatedFillModelConfig(latency_bars=0),
    )


def _run(window: SimpleNamespace, splits: list[CorporateAction]) -> SimulationExecutionResult:
    context_builder = StrategyContextBuilder(
        market_bar_reader=None,  # type: ignore[arg-type]
        bars_dataset=None,  # type: ignore[arg-type]
        lookback_bars=2,
    )
    if splits:
        context_builder = context_builder.with_split_source(StaticSplitSource(splits))
    return _engine().execute(
        run_id=_RUN_ID,
        strategy=StubStrategy(strategy_id="stub", price_change_threshold=0.0),
        window=window,
        context_builder=context_builder,
        simulated_execution_service=_execution_service(),
        initial_cash=_INITIAL_CASH,
        corporate_actions=splits,
    )


def _equity(result: SimulationExecutionResult) -> list[float]:
    return [float(v) for v in result.equity_curve["equity"].tolist()]


class TestForwardSplitDuringAHold:
    # warmup 2 bars; the stub buys on bar 2 (101 -> 102); 10:1 split on bar 3 whose
    # raw prices are a tenth of the day before (+0.5% / +0.5% real moves).
    PRICES = [100.0, 101.0, 102.0, 10.25, 10.30]
    EX_DATE = (_BASE_TS + timedelta(days=3)).date()

    def test_equity_is_continuous_across_the_ex_date(self) -> None:
        result = _run(_window(self.PRICES), [_split("10", self.EX_DATE)])

        equity = _equity(result)
        # active bars: index 0 = bar 2 (buy), 1 = ex-date bar, 2 = day after
        assert 1.0 <= equity[1] / equity[0] <= 1.01, equity
        assert 1.0 <= equity[2] / equity[1] <= 1.01, equity
        # the only sell is the sizer trimming to its target (sized from the previous
        # bar's equity), a few post-split shares — not a crash-driven exit
        sells = result.trade_logs[result.trade_logs["side"] == "sell"]
        assert float(sells["quantity"].sum()) < 0.02 * 980

    def test_without_split_handling_the_old_books_showed_a_fake_crash(self) -> None:
        """Documents what 5d fixed: raw post-split marks against pre-split shares."""
        result = _run(_window(self.PRICES), [])
        equity = _equity(result)
        assert equity[1] / equity[0] < 0.2, equity

    def test_the_position_and_cost_basis_are_in_post_split_terms(self) -> None:
        result = _run(_window(self.PRICES), [_split("10", self.EX_DATE)])

        log = result.corporate_action_log
        assert len(log) == 1
        row = log.iloc[0]
        assert row["symbol"] == _SYMBOL
        assert row["action_type"] == CorporateActionType.SPLIT_FORWARD.value
        assert row["ex_date"] == self.EX_DATE
        assert row["quantity_after"] == row["quantity_before"] * 10
        assert row["avg_cost_after"] == pytest_approx(row["avg_cost_before"] / 10)
        assert row["cash_in_lieu"] == 0.0
        assert row["realized_pnl"] == 0.0

        ex_bar = result.positions[result.positions["timestamp"] == _BASE_TS + timedelta(days=3)]
        assert len(ex_bar) == 1
        # post-split share count (less the sizer's trim), post-split cost
        assert float(ex_bar.iloc[0]["quantity"]) >= 0.98 * row["quantity_after"]
        assert float(ex_bar.iloc[0]["avg_cost"]) < 11.0


class TestReverseSplitWithAFraction:
    # 1:4 reverse split on bar 3; raw prices quadruple (+0.25% real move).
    PRICES = [100.0, 101.0, 102.0, 409.0, 410.0]
    EX_DATE = (_BASE_TS + timedelta(days=3)).date()

    def test_fraction_is_paid_in_cash_at_the_ex_date_price(self) -> None:
        result = _run(_window(self.PRICES), [_split("0.25", self.EX_DATE)])

        row = result.corporate_action_log.iloc[0]
        before = row["quantity_before"]
        whole = math.floor(before * 0.25)
        fraction = before * 0.25 - whole
        assert row["quantity_after"] == whole
        assert row["cash_in_lieu"] == pytest_approx(fraction * 409.0)
        # paid at 409 against a post-split cost of 408: the fraction's gain is realised
        assert row["realized_pnl"] == pytest_approx(fraction * (409.0 - 408.0))

        equity = _equity(result)
        assert 1.0 <= equity[1] / equity[0] <= 1.01, equity


class TestNoPosition:
    def test_a_split_before_any_position_changes_nothing(self) -> None:
        prices = [100.0, 101.0, 102.0, 103.0]
        ex_date = (_BASE_TS + timedelta(days=1)).date()  # during warmup, nothing held
        result = _run(_window(prices), [_split("2", ex_date)])

        assert result.corporate_action_log.empty
        assert list(result.corporate_action_log.columns)[:3] == ["run_id", "strategy_id", "symbol"]
        assert _equity(result)[0] > 0


def pytest_approx(value: float, rel: float = 1e-6):
    import pytest

    return pytest.approx(value, rel=rel, abs=1e-9)
