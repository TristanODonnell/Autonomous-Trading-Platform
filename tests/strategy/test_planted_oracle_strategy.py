"""The planted oracle (research plan, "Planted controls, M1"): its answer key, its calls,
its signals, and what the simulator hands it.

The control is only worth running if three things hold, and each is checked here:
it is told exactly the move it then trades (first bar's close to last bar's close, the
prices the research fill model fills at); its calls are right the share of the time its
``accuracy`` says, and at 0.5 they carry no information about the outcome; and only a
planted control is ever handed the run's window.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
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
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.planted_control import (
    PlantedControlNotBoundError,
)
from autonomous_trading_platform.strategy.implementations.planted_oracle_strategy import (
    PlantedOracleStrategy,
    oracle_is_right,
    session_calls,
    session_date,
)
from tests.utilities.factories import make_five_minute_bar

_RUN_ID = UUID("bbbbbbbb-0000-0000-0000-000000000001")
_CASH = 100_000.0
# 14:30 UTC is 09:30 in New York in January.
_DAY1 = datetime(2025, 1, 6, 14, 30, tzinfo=UTC)

# Per symbol, per session, the closes of its bars (four 5-minute bars a session).
# AAPL rises on day 1, falls on day 2, rises on day 3; MSFT the opposite.
_SESSIONS: dict[str, list[list[float]]] = {
    "AAPL": [[100, 101, 99, 104], [104, 103, 105, 100], [100, 100, 102, 103]],
    "MSFT": [[200, 199, 201, 195], [195, 197, 196, 199], [199, 200, 198, 190]],
}


def _bars() -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for symbol, sessions in _SESSIONS.items():
        bars = []
        for day, closes in enumerate(sessions):
            start = _DAY1 + timedelta(days=day)
            for i, close in enumerate(closes):
                bars.append(
                    make_five_minute_bar(
                        timestamp=start + timedelta(minutes=5 * i),
                        symbol=symbol,
                        open_price=str(close),
                        high_price=str(close),
                        low_price=str(close),
                        close_price=str(close),
                        volume=1_000_000,
                    )
                )
        out[symbol] = bars
    return out


def _window(bars_by_symbol: dict[str, list[Any]]) -> SimpleNamespace:
    timeline = sorted({bar.timestamp for bars in bars_by_symbol.values() for bar in bars})
    by_ts: dict[datetime, dict[str, Any]] = {ts: {} for ts in timeline}
    for symbol, bars in bars_by_symbol.items():
        for bar in bars:
            by_ts[bar.timestamp][symbol] = bar
    return SimpleNamespace(
        symbols=sorted(bars_by_symbol),
        timeline=timeline,
        bars_by_symbol=bars_by_symbol,
        bars_by_timestamp=by_ts,
        warmup_timestamps=set(),
        is_warmup=lambda ts: False,
    )


def _context(symbol: str, ts: datetime) -> StrategyContext:
    return StrategyContext(
        strategy_id="oracle",
        run_id=_RUN_ID,
        symbol=symbol,
        evaluation_timestamp=ts,
        bar_timestamp=ts,
    )


# --- the answer key -------------------------------------------------------------------


def test_the_key_is_first_close_to_last_close_per_name_and_session() -> None:
    calls = session_calls(_bars(), accuracy=1.0, seed=0)

    days = [date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)]
    assert {(s, d): c.rose for (s, d), c in calls.items()} == {
        ("AAPL", days[0]): True,
        ("AAPL", days[1]): False,
        ("AAPL", days[2]): True,
        ("MSFT", days[0]): False,
        ("MSFT", days[1]): True,
        ("MSFT", days[2]): False,
    }
    first = calls[("AAPL", days[0])]
    assert first.first_bar == _DAY1
    assert first.last_bar == _DAY1 + timedelta(minutes=15)


def test_an_unchanged_session_has_not_risen_and_a_one_bar_session_is_left_out() -> None:
    flat = [
        make_five_minute_bar(timestamp=_DAY1 + timedelta(minutes=5 * i), close_price="50")
        for i in range(3)
    ]
    lone = [make_five_minute_bar(timestamp=_DAY1 + timedelta(days=1), close_price="50")]

    calls = session_calls({"AAPL": flat + lone}, accuracy=1.0, seed=0)

    assert list(calls) == [("AAPL", date(2025, 1, 6))]
    assert calls[("AAPL", date(2025, 1, 6))].rose is False


def test_warm_up_bars_are_not_read() -> None:
    bars = _bars()
    # Skip day 1's last AAPL bar: its session now ends on the 99 close, below the open.
    skipped = {_DAY1 + timedelta(minutes=15)}

    calls = session_calls(bars, accuracy=1.0, seed=0, skip_timestamps=skipped)

    assert calls[("AAPL", date(2025, 1, 6))].rose is False


def test_sessions_are_new_york_dates() -> None:
    # 23:30 New York time on 6 January is already 7 January in UTC.
    assert session_date(datetime(2025, 1, 7, 4, 30, tzinfo=UTC)) == date(2025, 1, 6)


# --- the calls -------------------------------------------------------------------------


@pytest.mark.parametrize("accuracy", [0.0, 0.5, 0.52, 0.55, 0.6, 1.0])
def test_the_oracle_is_right_the_share_of_sessions_its_accuracy_says(accuracy: float) -> None:
    start = date(2020, 1, 1)
    draws = [
        oracle_is_right(
            seed=1, symbol=f"S{n % 40}", session=start + timedelta(days=n // 40), accuracy=accuracy
        )
        for n in range(40_000)
    ]
    # Deterministic draws; 40,000 of them put the share within 0.01 of the accuracy.
    assert abs(sum(draws) / len(draws) - accuracy) < 0.01


def test_a_session_gets_the_same_call_whatever_window_it_is_in() -> None:
    """Selection, the folds, stress and the holdout each load their own window: a
    session's call must not depend on which."""
    bars = _bars()
    whole = session_calls(bars, accuracy=0.55, seed=3)
    day3_only = session_calls(
        {s: [b for b in bs if b.timestamp.date() == date(2025, 1, 8)] for s, bs in bars.items()},
        accuracy=0.55,
        seed=3,
    )

    for key, call in day3_only.items():
        assert whole[key] == call


def test_the_three_oracles_are_nested_on_one_seed() -> None:
    """On any session, a stronger oracle is right whenever a weaker one with the same seed
    is: the planted strengths differ in edge alone."""
    start = date(2021, 1, 1)
    for n in range(5_000):
        symbol, session = f"S{n % 25}", start + timedelta(days=n // 25)
        weak = oracle_is_right(seed=1, symbol=symbol, session=session, accuracy=0.52)
        mid = oracle_is_right(seed=1, symbol=symbol, session=session, accuracy=0.55)
        strong = oracle_is_right(seed=1, symbol=symbol, session=session, accuracy=0.60)
        assert (not weak or mid) and (not mid or strong)


def test_at_one_half_the_call_carries_no_information_about_the_outcome() -> None:
    """The negative control: P(call rise | rose) equals P(call rise | fell)."""
    rng = random.Random(7)
    bars_by_symbol: dict[str, list[Any]] = {}
    for s in range(20):
        bars = []
        for day in range(300):
            start = _DAY1 + timedelta(days=day)
            open_, close = 100.0, 100.0 + rng.choice([-1.0, 1.0])
            for i, px in enumerate((open_, close)):
                bars.append(
                    make_five_minute_bar(
                        timestamp=start + timedelta(minutes=5 * i),
                        symbol=f"S{s}",
                        close_price=str(px),
                    )
                )
        bars_by_symbol[f"S{s}"] = bars

    calls = session_calls(bars_by_symbol, accuracy=0.5, seed=11).values()
    rose = [c for c in calls if c.rose]
    fell = [c for c in calls if not c.rose]
    p_given_rose = sum(c.called_rise for c in rose) / len(rose)
    p_given_fell = sum(c.called_rise for c in fell) / len(fell)

    assert len(rose) > 2_500 and len(fell) > 2_500
    assert abs(p_given_rose - 0.5) < 0.03
    assert abs(p_given_fell - 0.5) < 0.03


def test_other_seeds_make_other_calls() -> None:
    a = session_calls(_bars(), accuracy=0.5, seed=11)
    b = session_calls(_bars(), accuracy=0.5, seed=12)
    many = [
        oracle_is_right(seed=s, symbol="AAPL", session=date(2025, 1, 6), accuracy=0.5)
        for s in range(11, 31)
    ]
    assert a.keys() == b.keys()
    assert len(set(many)) == 2


# --- the signals -----------------------------------------------------------------------


def test_buys_at_the_first_bar_sells_at_the_last_and_is_silent_between() -> None:
    oracle = PlantedOracleStrategy("oracle", accuracy=1.0, seed=0)
    oracle.bind_run_window(_window(_bars()))

    def direction(symbol: str, minutes: int, day: int = 0) -> SignalDirection | None:
        signal = oracle.evaluate_symbol(
            _context(symbol, _DAY1 + timedelta(days=day, minutes=minutes))
        )
        return None if signal is None else signal.direction

    assert direction("AAPL", 0) == SignalDirection.BUY  # rises, called right
    assert direction("AAPL", 5) is None
    assert direction("AAPL", 10) is None
    assert direction("AAPL", 15) == SignalDirection.SELL
    assert direction("MSFT", 0) is None  # falls: not bought
    assert direction("MSFT", 15) == SignalDirection.SELL  # the exit is always given


def test_a_wrong_call_buys_the_falling_name() -> None:
    oracle = PlantedOracleStrategy("oracle", accuracy=0.0, seed=0)
    oracle.bind_run_window(_window(_bars()))

    buy = oracle.evaluate_symbol(_context("MSFT", _DAY1))
    skip = oracle.evaluate_symbol(_context("AAPL", _DAY1))

    assert isinstance(buy, Signal) and buy.direction == SignalDirection.BUY
    assert skip is None


def test_a_control_never_handed_its_window_refuses_to_trade() -> None:
    """Started anywhere but the research simulator (the live cycle never binds a window),
    the control raises rather than trade."""
    oracle = PlantedOracleStrategy("oracle", accuracy=0.6, seed=1)

    with pytest.raises(PlantedControlNotBoundError):
        oracle.evaluate_symbol(_context("AAPL", _DAY1))


def test_accuracy_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValueError):
        PlantedOracleStrategy("oracle", accuracy=1.1)


# --- inside the simulator --------------------------------------------------------------


def _engine_run(strategy: BaseStrategy) -> Any:
    cost = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"), min_commission=Decimal("0")
        ),
        slippage_model=SlippageModel(config=SlippageModelConfig(slippage_rate=Decimal("0"))),
    )
    execution = SimulatedExecutionService(
        simulation_cost_model_service=cost,
        fill_model_config=SimulatedFillModelConfig(latency_bars=0),
    )
    execution.reset_for_run(rng=random.Random(0))
    engine = SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=LookaheadGuardService(),
        position_sizer=SimplePositionSizer(total_capital=_CASH, universe_size=2),
    )
    return engine.execute(
        run_id=_RUN_ID,
        strategy=strategy,
        window=_window(_bars()),
        context_builder=StrategyContextBuilder(
            market_bar_reader=None,  # type: ignore[arg-type]
            bars_dataset=None,  # type: ignore[arg-type]
            lookback_bars=0,
        ),
        simulated_execution_service=execution,
        initial_cash=_CASH,
    )


def test_the_simulator_hands_the_window_to_a_planted_control() -> None:
    """A perfect oracle, run by the real engine at zero cost: it buys only the sessions
    that rise, at the first bar's close, sells at the last bar's close, and makes money
    on every round trip. So it is told exactly the prices it trades at."""
    result = _engine_run(PlantedOracleStrategy("oracle", accuracy=1.0, seed=0))
    trades = result.trade_logs

    buys = trades[trades["side"] == "buy"]
    sells = trades[trades["side"] == "sell"]
    rising = {
        (s, _DAY1 + timedelta(days=d))
        for s, sessions in _SESSIONS.items()
        for d, closes in enumerate(sessions)
        if closes[-1] > closes[0]
    }
    assert {(r.symbol, r.timestamp) for r in buys.itertuples()} == rising
    assert {(r.symbol, r.timestamp - timedelta(minutes=15)) for r in sells.itertuples()} == rising
    for buy in buys.itertuples():
        sell = sells[
            (sells["symbol"] == buy.symbol)
            & (sells["timestamp"] == buy.timestamp + timedelta(minutes=15))
        ].iloc[0]
        day = (buy.timestamp - _DAY1).days
        assert buy.price == _SESSIONS[buy.symbol][day][0]
        assert sell["price"] == _SESSIONS[buy.symbol][day][-1]
        assert sell["price"] > buy.price
    equity = result.equity_curve["equity"]
    assert float(equity.iloc[-1]) > _CASH


class _NotAControl(BaseStrategy):
    """A normal strategy that happens to have a method of the same name."""

    def __init__(self) -> None:
        self.handed: list[Any] = []

    @property
    def strategy_id(self) -> str:
        return "not_a_control"

    def bind_run_window(self, window: Any) -> None:
        self.handed.append(window)

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        return None


def test_the_simulator_hands_nothing_to_any_other_strategy() -> None:
    """The one exception to "bars before the evaluation time only" is gated on the
    class, not on a method name a strategy might share."""
    strategy = _NotAControl()

    _engine_run(strategy)

    assert strategy.handed == []
