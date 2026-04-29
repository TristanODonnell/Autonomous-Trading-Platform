from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)


def test_slippage_model_applies_buy_slippage_above_market_price():
    model = SlippageModel(
        config=SlippageModelConfig(
            slippage_rate=Decimal("0.001"),
        )
    )

    fill_price = model.calculate_fill_price(
        side=Side.BUY,
        market_price=Decimal("100"),
    )

    assert fill_price == Decimal("100.100")


def test_slippage_model_applies_sell_slippage_below_market_price():
    model = SlippageModel(
        config=SlippageModelConfig(
            slippage_rate=Decimal("0.001"),
        )
    )

    fill_price = model.calculate_fill_price(
        side=Side.SELL,
        market_price=Decimal("100"),
    )

    assert fill_price == Decimal("99.900")


def test_slippage_model_uses_configured_slippage_rate():
    model = SlippageModel(
        config=SlippageModelConfig(
            slippage_rate=Decimal("0.0005"),
        )
    )

    fill_price = model.calculate_fill_price(
        side=Side.BUY,
        market_price=Decimal("200"),
    )

    assert fill_price == Decimal("200.100")
