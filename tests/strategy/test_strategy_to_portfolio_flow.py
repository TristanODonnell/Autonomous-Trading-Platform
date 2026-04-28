from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.safety.services.pre_trade_risk_service import (
    PreTradeRiskService,
)
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000601")


@dataclass
class FakeBar:
    timestamp: datetime


class StubContextBuilder:
    def __init__(self, context_by_symbol: dict[str, StrategyContext | None]) -> None:
        self.context_by_symbol = context_by_symbol

    def build(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        symbol: str,
        bar_timestamp: datetime,
        evaluation_timestamp: datetime,
    ) -> StrategyContext | None:
        return self.context_by_symbol.get(symbol)


class StubMarketBarReader:
    def __init__(self, bars_by_symbol: dict[str, list[FakeBar]]) -> None:
        self.bars_by_symbol = bars_by_symbol

    def get_bars_up_to_timestamp(
        self,
        symbol: str,
        end_timestamp: datetime,
        lookback_bars: int,
    ) -> list[FakeBar]:
        return self.bars_by_symbol.get(symbol, [])


class StubUniverseReader:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols

    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]:
        return list(self.symbols)


class StubStrategy(BaseStrategy):
    @property
    def strategy_id(self) -> str:
        return "strategy-alpha"

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        return Signal(
            signal_id=UUID("00000000-0000-0000-0000-000000000602"),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=self.strategy_id,
            symbol=context.symbol,
            direction=SignalDirection.BUY,
            confidence=1.0,
        )


class FakeRiskService(PreTradeRiskService):
    def __init__(self) -> None:
        self.checked: list[tuple[OrderIntent, datetime]] = []

    def assert_order_allowed(self, order_intent: OrderIntent, now: datetime) -> None:
        self.checked.append((order_intent, now))


def _bars(count: int, *, end_timestamp: datetime) -> list[FakeBar]:
    start = end_timestamp - timedelta(minutes=(count - 1) * 5)
    return [FakeBar(timestamp=start + timedelta(minutes=i * 5)) for i in range(count)]


def test_strategy_evaluation_result_flows_into_portfolio_construction() -> None:
    bar_timestamp = datetime(2025, 1, 2, 15, 35, tzinfo=UTC)
    evaluation_timestamp = datetime(2025, 1, 2, 15, 36, tzinfo=UTC)

    context_builder = StubContextBuilder(
        {
            "AAPL": StrategyContext(
                run_id=RUN_ID,
                strategy_id="strategy-alpha",
                symbol="AAPL",
                bar_timestamp=bar_timestamp,
                evaluation_timestamp=evaluation_timestamp,
                bars=_bars(3, end_timestamp=bar_timestamp),
            )
        }
    )

    evaluation_service = StrategyEvaluationService(
        context_builder=context_builder,
        universe_reader=StubUniverseReader(["AAPL"]),
        strategy=StubStrategy(),
    )

    evaluation_result = evaluation_service.evaluate(
        bar_timestamp=bar_timestamp,
        run_id=RUN_ID,
        evaluation_timestamp=evaluation_timestamp,
    )

    risk_service = FakeRiskService()
    portfolio_service = PortfolioConstructionService(pre_trade_risk_service=risk_service)

    intents = list(
        portfolio_service.generate_order_intents(
            signals=evaluation_result.signals,
            positions={"AAPL": 0},
            prices={"AAPL": 150.0},
            run_id=RUN_ID,
            strategy_id=evaluation_result.strategy_id,
            bar_timestamp=bar_timestamp,
            now=evaluation_timestamp,
        )
    )

    assert len(evaluation_result.signals) == 1
    assert evaluation_result.signals[0].symbol == "AAPL"

    assert len(intents) == 1
    assert intents[0].symbol == "AAPL"
    assert len(risk_service.checked) == 1
