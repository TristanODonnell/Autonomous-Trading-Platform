from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)


def make_bar(*, symbol: str = "AAPL", close: Decimal = Decimal("100")):
    return SimpleNamespace(
        symbol=symbol,
        timestamp=datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
        close=close,
    )


def make_intent(
    *,
    side: Side,
    symbol: str = "AAPL",
    qty: Decimal | None = Decimal("10"),
):
    return SimpleNamespace(
        intent_id=str(uuid4()),
        run_id=uuid4(),
        symbol=symbol,
        side=side,
        qty=qty,
        order_type="market",
    )


def make_service() -> SimulatedExecutionService:
    cost_model = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0.01"),
            min_commission=Decimal("1.00"),
        ),
        slippage_model=SlippageModel(
            config=SlippageModelConfig(slippage_rate=Decimal("0.001")),
        ),
    )

    return SimulatedExecutionService(
        simulation_cost_model_service=cost_model,
        fill_model_config=SimulatedFillModelConfig(),
    )


def test_fill_applies_buy_slippage_and_fees():
    service = make_service()

    intent = make_intent(side=Side.BUY)
    bar = make_bar(close=Decimal("100"))

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": bar},
    )

    assert len(fills) == 1

    fill = fills[0]

    assert fill.symbol == "AAPL"
    assert fill.side == Side.BUY
    assert fill.quantity == Decimal("10")

    # 10 bps = 0.10% worse than reference price
    assert fill.price == Decimal("100.100")
    assert fill.price > bar.close

    # max(min_commission, commission_per_share * qty)
    assert fill.fees == Decimal("1.00")


def test_fill_applies_sell_slippage_and_fees():
    service = make_service()

    intent = make_intent(side=Side.SELL)
    bar = make_bar(close=Decimal("100"))

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": bar},
    )

    assert len(fills) == 1

    fill = fills[0]

    assert fill.symbol == "AAPL"
    assert fill.side == Side.SELL
    assert fill.quantity == Decimal("10")

    # Sell fills below reference price after slippage
    assert fill.price == Decimal("99.900")
    assert fill.price < bar.close

    assert fill.fees == Decimal("1.00")


def test_fill_skips_intent_when_bar_missing():
    service = make_service()

    intent = make_intent(side=Side.BUY, symbol="MSFT")

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": make_bar(symbol="AAPL")},
    )

    assert fills == []


def test_fill_skips_intent_when_quantity_missing():
    service = make_service()

    intent = make_intent(side=Side.BUY, qty=None)

    fills = service.fill(
        order_intents=[intent],
        bars_at_timestamp={"AAPL": make_bar()},
    )

    assert fills == []
