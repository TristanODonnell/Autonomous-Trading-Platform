from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import Side, SignalDirection
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.position_sizer import PositionSizer
from autonomous_trading_platform.goverance.models.governance_state import GovernanceState
from autonomous_trading_platform.safety.services.pre_trade_risk_service import (
    PreTradeRiskService,
)


class FakePositionSizer:
    def compute_quantity(
        self,
        *,
        strategy_id: str,
        approval_status: object,
        symbol: str,
        current_price: object,
        performance_tier: str | None = None,
        vol_scalar: object | None = None,
    ) -> int:
        if symbol == "TSLA":
            return 0
        return 10


@dataclass
class FakeRiskService(PreTradeRiskService):
    checked_intents: list[OrderIntent]
    checked_times: list[datetime]

    def __init__(self) -> None:
        self.checked_intents = []
        self.checked_times = []

    def assert_order_allowed(self, order_intent: OrderIntent, now: datetime) -> None:
        self.checked_intents.append(order_intent)
        self.checked_times.append(now)


def make_signal(*, symbol: str, direction: str) -> Signal:
    direction_map = {
        "long": SignalDirection.BUY,
        "sell": SignalDirection.SELL,
        "flat": SignalDirection.FLAT,
    }

    return Signal(
        signal_id=UUID("00000000-0000-0000-0000-000000000601"),
        run_id=UUID("00000000-0000-0000-0000-000000000602"),
        timestamp=datetime(2025, 1, 2, 15, 30, tzinfo=UTC),
        bar_timestamp=datetime(2025, 1, 2, 15, 30, tzinfo=UTC),
        strategy_id="test-strategy",
        symbol=symbol,
        direction=direction_map[direction],
        confidence=1.0,
    )


@pytest.fixture
def risk_service() -> FakeRiskService:
    return FakeRiskService()


@pytest.fixture
def position_sizer() -> PositionSizer:
    return cast(PositionSizer, FakePositionSizer())


@pytest.fixture
def service(
    risk_service: FakeRiskService,
    position_sizer: PositionSizer,
) -> PortfolioConstructionService:
    return PortfolioConstructionService(
        pre_trade_risk_service=risk_service,
        position_sizer=position_sizer,
    )


@pytest.fixture
def run_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000501")


@pytest.fixture
def strategy_id() -> str:
    return "strategy-alpha"


@pytest.fixture
def bar_timestamp() -> datetime:
    return datetime(2025, 1, 2, 15, 30, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return datetime(2025, 1, 2, 15, 30, 45, tzinfo=UTC)


class TestPortfolioConstructionService:
    def test_build_order_intent_generates_deterministic_client_order_id_from_run_strategy_and_bar(
        self,
        service: PortfolioConstructionService,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: datetime,
    ) -> None:
        delta = {
            "symbol": "AAPL",
            "current_qty": 0,
            "target_qty": 10,
            "delta_qty": 10,
        }
        prices = {"AAPL": 150.0}

        first_now = datetime(2025, 1, 2, 15, 30, 1, tzinfo=UTC)
        second_now = datetime(2025, 1, 2, 15, 30, 59, tzinfo=UTC)

        first = service.build_order_intent(
            delta=delta,
            prices=prices,
            run_id=run_id,
            strategy_id=strategy_id,
            bar_timestamp=bar_timestamp,
            now=first_now,
        )
        second = service.build_order_intent(
            delta=delta,
            prices=prices,
            run_id=run_id,
            strategy_id=strategy_id,
            bar_timestamp=bar_timestamp,
            now=second_now,
        )

        # Intended behavior:
        # same run_id + strategy_id + bar_timestamp => same deterministic client_order_id
        assert first.client_order_id == second.client_order_id

    def test_generate_order_intents_sizes_relative_to_current_positions(
        self,
        service: PortfolioConstructionService,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: datetime,
        now: datetime,
    ) -> None:
        signals = [make_signal(symbol="AAPL", direction="long")]
        positions = {"AAPL": 4}
        prices = {"AAPL": 125.0}

        intents = list(
            service.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
                approval_status=GovernanceState.APPROVED_RESEARCH,
            )
        )

        assert len(intents) == 1
        intent = intents[0]

        # position_sizer currently targets +10 for a long signal
        # so delta should be 10 - 4 = 6
        assert intent.symbol == "AAPL"
        assert intent.side == Side.BUY
        assert intent.qty == 6

    def test_generate_order_intents_sell_signal_reduces_or_closes_long_position(
        self,
        service: PortfolioConstructionService,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: datetime,
        now: datetime,
    ) -> None:
        signals = [make_signal(symbol="TSLA", direction="sell")]
        positions = {"TSLA": 3}
        prices = {"TSLA": 210.0}

        intents = list(
            service.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
                approval_status=GovernanceState.APPROVED_RESEARCH,
            )
        )

        assert len(intents) == 1
        intent = intents[0]
        # SELL signal exits long position (target = 0)
        # so delta should be 0 - 3 = -3 => SELL 3
        assert intent.symbol == "TSLA"
        assert intent.side == Side.SELL
        assert intent.qty == 3

    def test_generate_order_intents_calls_pre_trade_risk_for_each_intent(
        self,
        service: PortfolioConstructionService,
        risk_service: FakeRiskService,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: datetime,
        now: datetime,
    ) -> None:
        signals = [
            make_signal(symbol="AAPL", direction="long"),
            make_signal(symbol="MSFT", direction="sell"),
        ]
        positions = {"AAPL": 0, "MSFT": 5}
        prices = {"AAPL": 100.0, "MSFT": 200.0}

        intents = list(
            service.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
                approval_status=GovernanceState.APPROVED_RESEARCH,
            )
        )

        assert len(intents) == 2
        assert len(risk_service.checked_intents) == 2
        assert risk_service.checked_intents == intents
        assert risk_service.checked_times == [now, now]

    def test_generate_order_intents_zero_target_weights_produce_no_intents(
        self,
        service: PortfolioConstructionService,
        risk_service: FakeRiskService,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: datetime,
        now: datetime,
    ) -> None:
        signals = [make_signal(symbol="AAPL", direction="flat")]
        positions = {"AAPL": 0}
        prices = {"AAPL": 100.0}

        intents = list(
            service.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
                approval_status=GovernanceState.APPROVED_RESEARCH,
            )
        )

        assert intents == []
        assert risk_service.checked_intents == []
        assert risk_service.checked_times == []

    def test_generate_order_intents_zero_delta_produces_no_intent_even_when_signal_exists(
        self,
        service: PortfolioConstructionService,
        risk_service: FakeRiskService,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: datetime,
        now: datetime,
    ) -> None:
        signals = [make_signal(symbol="AAPL", direction="long")]
        positions = {"AAPL": 10}
        prices = {"AAPL": 100.0}

        intents = list(
            service.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
                approval_status=GovernanceState.APPROVED_RESEARCH,
            )
        )

        assert intents == []
        assert risk_service.checked_intents == []
