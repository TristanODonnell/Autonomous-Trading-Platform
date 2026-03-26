from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000301")


@dataclass
class FakeBar:
    timestamp: datetime


class StubMarketBarReader:
    def __init__(self, bars_by_symbol: dict[str, list[Any]]) -> None:
        self.bars_by_symbol = bars_by_symbol
        self.calls: list[tuple[str, datetime, int]] = []

    def get_bars_up_to_timestamp(
        self,
        symbol: str,
        end_timestamp: datetime,
        lookback_bars: int,
    ) -> list[Any]:
        self.calls.append((symbol, end_timestamp, lookback_bars))
        return self.bars_by_symbol.get(symbol, [])


class StubUniverseReader:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        self.calls: list[datetime] = []

    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]:
        self.calls.append(as_of)
        return list(self.symbols)


class StubStrategy(BaseStrategy):
    def __init__(self, signal_by_symbol: dict[str, Signal | None]) -> None:
        self._signal_by_symbol = signal_by_symbol
        self.contexts: list[StrategyContext] = []

    @property
    def strategy_id(self) -> str:
        return "test_strategy_v1"

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        self.contexts.append(context)
        return self._signal_by_symbol.get(context.symbol)


def _bars(count: int, *, end_timestamp: datetime) -> list[FakeBar]:
    start = end_timestamp - timedelta(minutes=(count - 1) * 5)
    return [FakeBar(timestamp=start + timedelta(minutes=i * 5)) for i in range(count)]


class TestStrategyEvaluationService:
    def test_evaluates_only_symbols_with_sufficient_lookback(self) -> None:
        bar_timestamp = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)
        evaluation_timestamp = datetime(2025, 1, 1, 15, 36, tzinfo=UTC)

        market_bar_reader = StubMarketBarReader(
            bars_by_symbol={
                "AAPL": _bars(3, end_timestamp=bar_timestamp),
                "MSFT": _bars(2, end_timestamp=bar_timestamp),
            }
        )
        universe_reader = StubUniverseReader(["AAPL", "MSFT"])
        aapl_signal = Signal(
            signal_id=UUID("00000000-0000-0000-0000-000000000401"),
            run_id=RUN_ID,
            timestamp=evaluation_timestamp,
            bar_timestamp=bar_timestamp,
            strategy_id="test_strategy_v1",
            symbol="AAPL",
            direction=SignalDirection.BUY,
            confidence=1.0,
        )
        strategy = StubStrategy(signal_by_symbol={"AAPL": aapl_signal, "MSFT": None})
        service = StrategyEvaluationService(
            market_bar_reader=market_bar_reader,
            universe_reader=universe_reader,
            strategy=strategy,
            lookback_bars=3,
        )

        result = service.evaluate(
            bar_timestamp=bar_timestamp,
            run_id=RUN_ID,
            evaluation_timestamp=evaluation_timestamp,
        )

        assert result.strategy_id == "test_strategy_v1"
        assert result.bar_timestamp == bar_timestamp
        assert len(result.signals) == 1
        signal = result.signals[0]
        assert signal.symbol == "AAPL"
        assert signal.direction == SignalDirection.BUY
        assert strategy.contexts[0].run_id == RUN_ID
        assert strategy.contexts[0].bar_timestamp == bar_timestamp
        assert strategy.contexts[0].evaluation_timestamp == evaluation_timestamp
        assert len(strategy.contexts[0].bars) == 3

    def test_returns_empty_signals_when_strategy_emits_none(self) -> None:
        bar_timestamp = datetime(2025, 1, 1, 15, 35, tzinfo=UTC)
        evaluation_timestamp = datetime(2025, 1, 1, 15, 36, tzinfo=UTC)

        market_bar_reader = StubMarketBarReader(
            bars_by_symbol={"AAPL": _bars(3, end_timestamp=bar_timestamp)}
        )
        universe_reader = StubUniverseReader(["AAPL"])
        strategy = StubStrategy(signal_by_symbol={"AAPL": None})

        service = StrategyEvaluationService(
            market_bar_reader=market_bar_reader,
            universe_reader=universe_reader,
            strategy=strategy,
            lookback_bars=3,
        )

        result = service.evaluate(
            bar_timestamp=bar_timestamp,
            run_id=RUN_ID,
            evaluation_timestamp=evaluation_timestamp,
        )

        assert result.signals == []
