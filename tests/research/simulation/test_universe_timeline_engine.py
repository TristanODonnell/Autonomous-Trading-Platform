"""
The simulation engine following a universe timeline (research plan, package B).

Given a timeline, the engine's universe is the list in force at each bar's time, by the
rule of the trading platform (``PortfolioConstructionService.generate_order_intents``):

- the strategy is asked only about names in the list in force;
- a held name in the list that gave no signal is kept;
- a held name that is not in the list is sold in full, and when it has no price on
  that bar the sale is tried again on the next bar;
- the budget is split by the number of names in the list in force.

Given no timeline, nothing changes: ``window.symbols`` is the universe for the whole run.

Scenario (lookback 2, StubStrategy threshold 0.5, so the signal on bar t comes from the
closes of bars t-2 and t-1, and with latency 0 it fills at the close of bar t):

  bar      0     1     2      3      4      5      6      7      8      9
  LEAVER   100   101   101.2  101.4  101.6  101.8  102.0  102.2  102.4  102.6
  KEPT     100   101   101.2  101.4  101.6  101.8  102.0  102.2  102.4  102.6
  ENTRANT  50    50    51     51.1   51.2   51.3   52.3   52.4   52.5   52.6

  LEAVER and KEPT give one BUY, on bar 2. ENTRANT gives a BUY on bar 3 and on bar 7.
  List one, bars 0 to 4: LEAVER, KEPT. List two, from bar 5: KEPT, ENTRANT.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimeline,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
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

_LEAVER = "LEAVER"
_KEPT = "KEPT"
_ENTRANT = "ENTRANT"
_BASE_TS = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000009920")
_INITIAL_CASH = 100_000.0
# The sizer's budget is half the cash, so no buy in the scenario is short of cash.
_SIZER_CAPITAL = 50_000.0

_RISING = [100.0, 101.0, 101.2, 101.4, 101.6, 101.8, 102.0, 102.2, 102.4, 102.6]
_CLOSES: dict[str, list[float | None]] = {
    _LEAVER: list(_RISING),
    _KEPT: list(_RISING),
    _ENTRANT: [50.0, 50.0, 51.0, 51.1, 51.2, 51.3, 52.3, 52.4, 52.5, 52.6],
}
_BUY_BAR = 2
_BUY_PRICE = 101.2
_ROTATION_BAR = 5
_CLOSE_AT_ROTATION = 101.8
_ENTRANT_SIGNAL_BEFORE_ENTRY = 3
_ENTRANT_BUY_BAR = 7
_LAST_BAR = 9
# 50,000 split by the two names in force, at 101.2 a share; by three names it is 164.
_SHARES_SPLIT_BY_TWO = 247
_SHARES_SPLIT_BY_THREE = 164

_LIST_ONE = "universe_list_one"
_LIST_TWO = "universe_list_two"
_TIMELINE_ID = "universe_timeline_test"


def _ts(bar_index: int) -> datetime:
    return _BASE_TS + timedelta(minutes=5 * bar_index)


def _window(
    closes: dict[str, list[float | None]] | None = None,
    *,
    warmup_bars: int = 0,
    volume: int = 100_000,
) -> Any:
    closes = closes if closes is not None else _CLOSES
    bar_count = len(next(iter(closes.values())))
    timeline = [_ts(i) for i in range(bar_count)]
    bars_by_symbol: dict[str, list[Any]] = {symbol: [] for symbol in closes}
    bars_by_timestamp: dict[datetime, dict[str, Any]] = {ts: {} for ts in timeline}
    for symbol, series in closes.items():
        for ts, close in zip(timeline, series, strict=True):
            if close is None:
                continue
            bar = make_five_minute_bar(
                timestamp=ts,
                symbol=symbol,
                open_price=str(close),
                high_price=str(close),
                low_price=str(close),
                close_price=str(close),
                volume=volume,
            )
            bars_by_symbol[symbol].append(bar)
            bars_by_timestamp[ts][symbol] = bar
    warmup = set(timeline[:warmup_bars])
    return SimpleNamespace(
        symbols=sorted(closes),
        timeline=timeline,
        bars_by_symbol=bars_by_symbol,
        bars_by_timestamp=bars_by_timestamp,
        warmup_timestamps=warmup,
        is_warmup=lambda ts: ts in warmup,
    )


def _period(
    version_id: str, symbols: list[str], *, start: datetime, end: datetime | None
) -> UniverseTimelinePeriod:
    return UniverseTimelinePeriod(
        universe_version_id=version_id,
        effective_from=start,
        effective_to=end,
        members=[UniverseTimelineMember(symbol=symbol) for symbol in symbols],
    )


def _timeline(periods: list[UniverseTimelinePeriod]) -> UniverseTimeline:
    return UniverseTimeline(
        dataset_version_id=_TIMELINE_ID,
        window_start=_BASE_TS.date(),
        content_hash="not-checked-by-the-engine",
        periods=periods,
    )


def _rotating_timeline() -> UniverseTimeline:
    """List one up to the rotation bar, list two from it on."""
    return _timeline(
        [
            _period(
                _LIST_ONE,
                [_LEAVER, _KEPT],
                start=_BASE_TS - timedelta(days=1),
                end=_ts(_ROTATION_BAR),
            ),
            _period(_LIST_TWO, [_KEPT, _ENTRANT], start=_ts(_ROTATION_BAR), end=None),
        ]
    )


def _one_list_timeline(start: datetime) -> UniverseTimeline:
    return _timeline([_period(_LIST_ONE, [_LEAVER, _KEPT, _ENTRANT], start=start, end=None)])


def _run(
    window: Any | None = None,
    *,
    timeline: UniverseTimeline | None,
    universe_size: int = 3,
    latency_bars: int = 0,
    max_volume_participation_rate: float | None = None,
) -> Any:
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
        position_sizer=SimplePositionSizer(
            total_capital=_SIZER_CAPITAL, universe_size=universe_size
        ),
    )
    return engine.execute(
        run_id=_RUN_ID,
        strategy=StubStrategy(strategy_id="stub", price_change_threshold=0.5),
        window=window if window is not None else _window(),
        context_builder=StrategyContextBuilder(
            market_bar_reader=None,  # type: ignore[arg-type]
            bars_dataset=None,  # type: ignore[arg-type]
            lookback_bars=2,
        ),
        simulated_execution_service=SimulatedExecutionService(
            simulation_cost_model_service=cost_service,
            fill_model_config=SimulatedFillModelConfig(
                latency_bars=latency_bars,
                max_volume_participation_rate=max_volume_participation_rate,
            ),
        ),
        initial_cash=_INITIAL_CASH,
        universe_timeline=timeline,
    )


def _trades(result: Any, symbol: str, side: str | None = None) -> pd.DataFrame:
    frame = result.trade_logs
    rows = frame[frame["symbol"] == symbol]
    if side is not None:
        rows = rows[rows["side"] == side]
    return rows.reset_index(drop=True)


def _held(result: Any, symbol: str, bar_index: int) -> float:
    """Shares of ``symbol`` held at the end of a bar; 0 when there is no position row."""
    frame = result.positions
    rows = frame[(frame["timestamp"] == _ts(bar_index)) & (frame["symbol"] == symbol)]
    assert len(rows) <= 1
    return float(rows["quantity"].iloc[0]) if len(rows) else 0.0


def _signal_bars(result: Any, symbol: str) -> list[int]:
    frame = result.signal_log
    stamps = frame[frame["symbol"] == symbol]["timestamp"]
    return sorted(int((ts - _BASE_TS) / timedelta(minutes=5)) for ts in stamps)


_RESULT_FRAMES = (
    "trade_logs",
    "equity_curve",
    "per_bar_metrics",
    "positions",
    "signal_log",
    "dividend_log",
    "corporate_action_log",
)


def _assert_same_results(with_timeline: Any, without: Any) -> None:
    for name in _RESULT_FRAMES:
        pd.testing.assert_frame_equal(
            getattr(with_timeline, name), getattr(without, name), obj=name
        )


class TestOneListReproducesTheFixedListRun:
    """The null experiment: a timeline whose one list is the window's own names, in
    force on every bar, must change nothing. A difference here would come from the
    new code path itself and not from the universe changing over time."""

    def test_every_result_frame_is_identical(self) -> None:
        without = _run(timeline=None)
        with_timeline = _run(timeline=_one_list_timeline(_BASE_TS - timedelta(days=1)))

        # The scenario trades, so equal frames are not equal because they are empty.
        assert len(without.trade_logs) >= 3
        _assert_same_results(with_timeline, without)
        assert with_timeline.universe_exit_log.empty

    def test_warmup_bars_before_the_first_list_change_nothing(self) -> None:
        """Warmup bars trade nothing and may lie before the timeline begins: the list
        starts on the first bar that can trade."""
        without = _run(_window(warmup_bars=2), timeline=None)
        with_timeline = _run(_window(warmup_bars=2), timeline=_one_list_timeline(_ts(2)))

        assert len(without.trade_logs) >= 1
        _assert_same_results(with_timeline, without)

    def test_no_timeline_gives_an_empty_exit_log_with_its_columns(self) -> None:
        result = _run(timeline=None)

        assert result.universe_exit_log.empty
        assert "universe_version_id" in result.universe_exit_log.columns


class TestANameThatLeavesIsSold:
    def test_held_name_is_sold_in_full_on_the_first_bar_of_the_new_list(self) -> None:
        result = _run(timeline=_rotating_timeline())

        buys, sells = _trades(result, _LEAVER, "buy"), _trades(result, _LEAVER, "sell")
        assert len(buys) == 1 and len(sells) == 1, result.trade_logs
        assert buys["timestamp"].iloc[0] == _ts(_BUY_BAR)
        assert sells["timestamp"].iloc[0] == _ts(_ROTATION_BAR)
        assert float(sells["quantity"].iloc[0]) == float(buys["quantity"].iloc[0])
        assert float(sells["price"].iloc[0]) == pytest.approx(_CLOSE_AT_ROTATION)
        assert _held(result, _LEAVER, _ROTATION_BAR - 1) == _SHARES_SPLIT_BY_TWO
        assert _held(result, _LEAVER, _ROTATION_BAR) == 0.0

    def test_without_a_timeline_the_same_name_is_never_sold(self) -> None:
        """The control: the price path gives no sell signal, so the sale above comes
        from the timeline and from nothing else."""
        result = _run(timeline=None)

        assert len(_trades(result, _LEAVER, "buy")) == 1
        assert _trades(result, _LEAVER, "sell").empty
        assert _held(result, _LEAVER, _LAST_BAR) > 0

    def test_the_exit_is_recorded_with_the_list_that_dropped_the_name(self) -> None:
        result = _run(timeline=_rotating_timeline())

        log = result.universe_exit_log
        assert len(log) == 1, log
        row = log.iloc[0]
        assert row["symbol"] == _LEAVER
        assert row["timestamp"] == _ts(_ROTATION_BAR)
        assert float(row["quantity"]) == _SHARES_SPLIT_BY_TWO
        assert row["universe_version_id"] == _LIST_TWO

    def test_a_held_member_with_no_signal_is_kept(self) -> None:
        result = _run(timeline=_rotating_timeline())

        assert len(_trades(result, _KEPT, "buy")) == 1
        assert _trades(result, _KEPT, "sell").empty
        assert _held(result, _KEPT, _LAST_BAR) == _SHARES_SPLIT_BY_TWO

    def test_the_sale_waits_for_a_bar_when_the_name_has_no_price(self) -> None:
        """No bar on the rotation bar or the next one: no order can be priced, the
        position stays, and the sale happens on the first bar the name trades again."""
        closes = {symbol: list(series) for symbol, series in _CLOSES.items()}
        closes[_LEAVER][_ROTATION_BAR] = None
        closes[_LEAVER][_ROTATION_BAR + 1] = None
        result = _run(_window(closes), timeline=_rotating_timeline())

        sells = _trades(result, _LEAVER, "sell")
        assert len(sells) == 1, result.trade_logs
        assert sells["timestamp"].iloc[0] == _ts(_ROTATION_BAR + 2)
        assert float(sells["quantity"].iloc[0]) == _SHARES_SPLIT_BY_TWO
        assert _held(result, _LEAVER, _ROTATION_BAR) == _SHARES_SPLIT_BY_TWO
        assert _held(result, _LEAVER, _ROTATION_BAR + 1) == _SHARES_SPLIT_BY_TWO
        assert _held(result, _LEAVER, _ROTATION_BAR + 2) == 0.0
        assert list(result.universe_exit_log["timestamp"]) == [_ts(_ROTATION_BAR + 2)]

    def test_with_one_bar_of_latency_the_sale_fills_on_the_next_bar(self) -> None:
        result = _run(timeline=_rotating_timeline(), latency_bars=1)

        sells = _trades(result, _LEAVER, "sell")
        assert len(sells) == 1, result.trade_logs
        assert sells["timestamp"].iloc[0] == _ts(_ROTATION_BAR + 1)
        assert list(result.universe_exit_log["timestamp"]) == [_ts(_ROTATION_BAR)]
        assert _held(result, _LEAVER, _LAST_BAR) == 0.0

    def test_an_exit_filling_over_several_bars_is_ordered_once(self) -> None:
        """At most 100 shares fill per bar (0.1% of a 100,000-share bar), so the exit
        takes three bars. While it is still filling no second exit is ordered: the
        shares sold add up to the shares bought, and the position never goes short."""
        result = _run(timeline=_rotating_timeline(), max_volume_participation_rate=0.001)

        bought = float(_trades(result, _LEAVER, "buy")["quantity"].sum())
        sells = _trades(result, _LEAVER, "sell")
        assert bought == _SHARES_SPLIT_BY_TWO
        assert len(sells) == 3, result.trade_logs
        assert float(sells["quantity"].sum()) == bought
        assert len(result.universe_exit_log) == 1
        leaver_rows = result.positions[result.positions["symbol"] == _LEAVER]
        assert (leaver_rows["quantity"].astype(float) >= 0).all()
        assert _held(result, _LEAVER, _LAST_BAR) == 0.0


class TestSignalsOnlyForMembers:
    def test_a_name_not_yet_in_the_list_gives_no_signal_and_is_not_bought(self) -> None:
        result = _run(timeline=_rotating_timeline())

        assert _signal_bars(result, _ENTRANT) == [_ENTRANT_BUY_BAR]
        buys = _trades(result, _ENTRANT, "buy")
        assert len(buys) == 1, result.trade_logs
        assert buys["timestamp"].iloc[0] == _ts(_ENTRANT_BUY_BAR)

    def test_without_a_timeline_the_same_name_signals_on_the_earlier_bar_too(self) -> None:
        result = _run(timeline=None)

        assert _signal_bars(result, _ENTRANT) == [_ENTRANT_SIGNAL_BEFORE_ENTRY, _ENTRANT_BUY_BAR]

    def test_a_name_that_left_gives_no_signal_afterwards(self) -> None:
        """LEAVER falls by 2 after it left, which is a SELL signal for a member."""
        closes = {symbol: list(series) for symbol, series in _CLOSES.items()}
        closes[_LEAVER][_ROTATION_BAR + 1] = closes[_LEAVER][_ROTATION_BAR] - 2.0  # type: ignore[operator]
        dropped_bar = _ROTATION_BAR + 2

        with_timeline = _run(_window(closes), timeline=_rotating_timeline())
        without = _run(_window(closes), timeline=None)

        assert dropped_bar in _signal_bars(without, _LEAVER)
        assert _signal_bars(with_timeline, _LEAVER) == [_BUY_BAR]


class TestBudgetIsSplitByTheNamesInForce:
    def test_the_count_comes_from_the_list_in_force_not_from_the_names_loaded(self) -> None:
        """Three names are loaded and the sizer was built for three, but two are in
        force: each position is half the budget, as on the platform."""
        with_timeline = _run(timeline=_rotating_timeline(), universe_size=3)
        without = _run(timeline=None, universe_size=3)

        assert float(_trades(with_timeline, _LEAVER, "buy")["quantity"].iloc[0]) == (
            _SHARES_SPLIT_BY_TWO
        )
        assert float(_trades(without, _LEAVER, "buy")["quantity"].iloc[0]) == (
            _SHARES_SPLIT_BY_THREE
        )

    def test_sizer_takes_the_count_per_call(self) -> None:
        from autonomous_trading_platform.contracts.trading.signal import Signal

        sizer = SimplePositionSizer(total_capital=_SIZER_CAPITAL, universe_size=3)
        signal = Signal(
            signal_id=UUID("00000000-0000-0000-0000-000000009921"),
            run_id=_RUN_ID,
            timestamp=_ts(0),
            bar_timestamp=_ts(0),
            strategy_id="stub",
            symbol=_LEAVER,
            direction=SignalDirection.BUY,
            confidence=0.55,
            params={},
        )
        prices = {_LEAVER: _BUY_PRICE}

        assert sizer.compute_targets(signals=[signal], prices=prices) == {
            _LEAVER: _SHARES_SPLIT_BY_THREE
        }
        assert sizer.compute_targets(signals=[signal], prices=prices, symbol_count=2) == {
            _LEAVER: _SHARES_SPLIT_BY_TWO
        }
        # The count given to one call does not stay for the next.
        assert sizer.compute_targets(signals=[signal], prices=prices) == {
            _LEAVER: _SHARES_SPLIT_BY_THREE
        }


class TestAWindowTheTimelineDoesNotCoverIsRefused:
    def test_a_bar_that_can_trade_before_the_first_list(self) -> None:
        with pytest.raises(ValueError, match="no list in force"):
            _run(timeline=_one_list_timeline(_ts(3)))

    def test_a_bar_in_a_gap_between_two_lists(self) -> None:
        timeline = _timeline(
            [
                _period(_LIST_ONE, [_LEAVER, _KEPT], start=_ts(0), end=_ts(4)),
                _period(_LIST_TWO, [_KEPT, _ENTRANT], start=_ts(6), end=None),
            ]
        )

        with pytest.raises(ValueError, match="no list in force"):
            _run(timeline=timeline)

    def test_a_member_whose_bars_were_not_loaded(self) -> None:
        timeline = _timeline(
            [_period(_LIST_ONE, [_LEAVER, _KEPT, "GHOST"], start=_ts(0), end=None)]
        )

        with pytest.raises(ValueError, match=r"did not load: \['GHOST'\]"):
            _run(timeline=timeline)

    def test_a_list_with_no_names(self) -> None:
        with pytest.raises(ValueError, match="has no names"):
            _run(timeline=_timeline([_period(_LIST_ONE, [], start=_ts(0), end=None)]))

    def test_nothing_is_refused_when_no_timeline_is_given(self) -> None:
        """A name loaded with no list anywhere is today's behaviour and stays allowed."""
        result = _run(timeline=None)

        assert not result.equity_curve.empty
