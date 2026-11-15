"""
Marking a held position when its symbol has no bar at a timestamp (research plan R10).

Real five-minute data is sparse: a thinly traded symbol often has no bar at a
timestamp where other symbols do. The engine must value a held position at its
latest close on such a bar. Valuing it at the price of its last fill makes equity
jump for one bar and jump back, which inflates bar-level volatility and pulls
Sharpe toward zero.

Timeline (lookback 2, latency 0, StubStrategy threshold 0.5):
  bar 0  HELD 100.0
  bar 1  HELD 101.0
  bar 2  HELD 101.4   signal from (100.0, 101.0) -> BUY, filled at this close
  bar 3  HELD 101.8   moves of 0.4 never reach the threshold: no more signals
  bar 4  HELD 102.2
  bar 5  HELD --      no bar
  bar 6  HELD 102.6
  bar 7  HELD 103.0
OTHER has a flat bar at every timestamp, is never traded, and keeps the timeline going.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
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
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar

_HELD = "HELD"
_OTHER = "OTHER"
_BASE_TS = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000009910")
_INITIAL_CASH = 100_000.0

# None = the symbol has no bar at that timestamp.
_HELD_CLOSES: list[float | None] = [100.0, 101.0, 101.4, 101.8, 102.2, None, 102.6, 103.0]
_FILL_BAR = 2
_BAR_BEFORE_GAP = 4
_GAP_BAR = 5
_BAR_AFTER_GAP = 6
_FILL_PRICE = 101.4
_CLOSE_BEFORE_GAP = 102.2


def _ts(bar_index: int) -> datetime:
    return _BASE_TS + timedelta(minutes=5 * bar_index)


def _bar(symbol: str, ts: datetime, close: float) -> Any:
    return make_five_minute_bar(
        timestamp=ts,
        symbol=symbol,
        open_price=str(close),
        high_price=str(close),
        low_price=str(close),
        close_price=str(close),
        volume=100_000,
    )


def _run() -> Any:
    timeline = [_ts(i) for i in range(len(_HELD_CLOSES))]
    bars_by_symbol: dict[str, list[Any]] = {_HELD: [], _OTHER: []}
    bars_by_timestamp: dict[datetime, dict[str, Any]] = {}
    for ts, held_close in zip(timeline, _HELD_CLOSES, strict=True):
        at_ts = {_OTHER: _bar(_OTHER, ts, 50.0)}
        bars_by_symbol[_OTHER].append(at_ts[_OTHER])
        if held_close is not None:
            at_ts[_HELD] = _bar(_HELD, ts, held_close)
            bars_by_symbol[_HELD].append(at_ts[_HELD])
        bars_by_timestamp[ts] = at_ts
    window = SimpleNamespace(
        symbols=[_HELD, _OTHER],
        timeline=timeline,
        bars_by_symbol=bars_by_symbol,
        bars_by_timestamp=bars_by_timestamp,
        warmup_timestamps=set(),
        is_warmup=lambda ts: False,
    )
    cost_service = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"), min_commission=Decimal("0")
        ),
        slippage_model=SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0"))),
    )
    engine = SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(total_capital=_INITIAL_CASH, universe_size=2),
    )
    return engine.execute(
        run_id=_RUN_ID,
        strategy=StubStrategy(strategy_id="stub", price_change_threshold=0.5),
        window=window,
        context_builder=StrategyContextBuilder(
            market_bar_reader=None,  # type: ignore[arg-type]
            bars_dataset=None,  # type: ignore[arg-type]
            lookback_bars=2,
        ),
        simulated_execution_service=SimulatedExecutionService(
            simulation_cost_model_service=cost_service,
            fill_model_config=SimulatedFillModelConfig(latency_bars=0),
        ),
        initial_cash=_INITIAL_CASH,
    )


def _equity_at(result: Any, bar_index: int) -> float:
    rows = result.equity_curve[result.equity_curve["timestamp"] == _ts(bar_index)]
    assert len(rows) == 1
    return float(rows["equity"].iloc[0])


def _position_row(result: Any, bar_index: int) -> Any:
    frame = result.positions
    rows = frame[(frame["timestamp"] == _ts(bar_index)) & (frame["symbol"] == _HELD)]
    assert len(rows) == 1
    return rows.iloc[0]


def _held_quantity(result: Any) -> float:
    """The scenario is one buy of HELD on the fill bar and nothing else."""
    trades = result.trade_logs
    assert len(trades) == 1, trades
    trade = trades.iloc[0]
    assert trade["symbol"] == _HELD
    assert trade["side"] == "buy"
    assert trade["timestamp"] == _ts(_FILL_BAR)
    assert float(trade["price"]) == pytest.approx(_FILL_PRICE)
    assert float(trade["quantity"]) > 0
    return float(trade["quantity"])


class TestEquityAcrossAMissingBar:
    def test_equity_does_not_move_on_a_bar_the_held_symbol_lacks(self) -> None:
        result = _run()
        quantity = _held_quantity(result)

        before, at_gap = _equity_at(result, _BAR_BEFORE_GAP), _equity_at(result, _GAP_BAR)

        # No price was observed on the gap bar, so the account is worth what it was.
        assert at_gap == pytest.approx(before, abs=1e-6), (
            f"equity moved by {at_gap - before:+.2f} on a bar with no new price; "
            f"marking at the last fill price would move it by "
            f"{quantity * (_FILL_PRICE - _CLOSE_BEFORE_GAP):+.2f}"
        )

    def test_equity_before_the_gap_carries_the_gain_since_the_fill(self) -> None:
        result = _run()
        quantity = _held_quantity(result)

        expected = _INITIAL_CASH + quantity * (_CLOSE_BEFORE_GAP - _FILL_PRICE)
        assert _equity_at(result, _BAR_BEFORE_GAP) == pytest.approx(expected, abs=1e-6)
        assert _equity_at(result, _GAP_BAR) == pytest.approx(expected, abs=1e-6)

    def test_equity_follows_the_market_again_after_the_gap(self) -> None:
        result = _run()
        quantity = _held_quantity(result)

        expected = _INITIAL_CASH + quantity * (102.6 - _FILL_PRICE)
        assert _equity_at(result, _BAR_AFTER_GAP) == pytest.approx(expected, abs=1e-6)

    def test_position_row_on_the_gap_bar_is_marked_at_the_latest_close(self) -> None:
        result = _run()
        quantity = _held_quantity(result)

        row = _position_row(result, _GAP_BAR)
        assert float(row["market_price"]) == pytest.approx(_CLOSE_BEFORE_GAP)
        assert float(row["market_value"]) == pytest.approx(quantity * _CLOSE_BEFORE_GAP)


class TestUnrealizedPnlFollowsTheClose:
    """Unrealized P&L was computed at the fill and never again, so every later row
    repeated the value from the fill bar (zero here: bought at that bar's close)."""

    def test_position_rows(self) -> None:
        result = _run()
        quantity = _held_quantity(result)

        assert float(_position_row(result, _FILL_BAR)["unrealized_pnl"]) == pytest.approx(0.0)
        for bar_index, close in ((3, 101.8), (_BAR_BEFORE_GAP, _CLOSE_BEFORE_GAP), (7, 103.0)):
            row = _position_row(result, bar_index)
            assert float(row["unrealized_pnl"]) == pytest.approx(
                quantity * (close - _FILL_PRICE)
            ), f"bar {bar_index}"
        # The gap bar keeps the value of the latest close.
        assert float(_position_row(result, _GAP_BAR)["unrealized_pnl"]) == pytest.approx(
            quantity * (_CLOSE_BEFORE_GAP - _FILL_PRICE)
        )

    def test_per_bar_metric_rows(self) -> None:
        result = _run()
        quantity = _held_quantity(result)

        metrics = result.per_bar_metrics
        rows = metrics[
            (metrics["timestamp"] == _ts(_BAR_BEFORE_GAP)) & (metrics["symbol"] == _HELD)
        ]
        assert len(rows) == 1
        assert float(rows["unrealized_pnl"].iloc[0]) == pytest.approx(
            quantity * (_CLOSE_BEFORE_GAP - _FILL_PRICE)
        )
