from decimal import Decimal

import pytest

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.execution.services.realised_slippage_service import (
    RealisedSlippageService,
)


def test_calculate_buy_slippage_positive_when_fill_above_reference() -> None:
    service = RealisedSlippageService()

    result = service.calculate(
        side=Side.BUY,
        reference_price=Money("100"),
        fill_price=Money("101.25"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("1.25")
    assert result.slippage_notional == Decimal("12.50")
    assert result.slippage_bps == Decimal("125")


def test_calculate_buy_slippage_negative_when_fill_below_reference() -> None:
    service = RealisedSlippageService()

    result = service.calculate(
        side=Side.BUY,
        reference_price=Money("100"),
        fill_price=Money("99.50"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("-0.50")
    assert result.slippage_notional == Decimal("-5.00")
    assert result.slippage_bps == Decimal("-50")


def test_calculate_sell_slippage_positive_when_fill_below_reference() -> None:
    service = RealisedSlippageService()

    result = service.calculate(
        side=Side.SELL,
        reference_price=Money("100"),
        fill_price=Money("99"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("1")
    assert result.slippage_notional == Decimal("10")
    assert result.slippage_bps == Decimal("100")


def test_calculate_sell_slippage_negative_when_fill_above_reference() -> None:
    service = RealisedSlippageService()

    result = service.calculate(
        side=Side.SELL,
        reference_price=Money("100"),
        fill_price=Money("100.75"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("-0.75")
    assert result.slippage_notional == Decimal("-7.50")
    assert result.slippage_bps == Decimal("-75")


def test_calculate_notional_slippage_equals_quantity_times_per_share_slippage() -> None:
    service = RealisedSlippageService()

    result = service.calculate(
        side=Side.BUY,
        reference_price=Money("250"),
        fill_price=Money("251.20"),
        quantity=Quantity("7"),
    )

    assert result.slippage_per_share == Decimal("1.20")
    assert result.slippage_notional == Decimal("8.40")
    assert result.slippage_notional == result.slippage_per_share * Decimal("7")


@pytest.mark.parametrize(
    ("reference_price", "fill_price", "quantity", "expected_message"),
    [
        (Money("0"), Money("100"), Quantity("10"), "reference_price must be positive"),
        (Money("-1"), Money("100"), Quantity("10"), "reference_price must be positive"),
        (Money("100"), Money("0"), Quantity("10"), "fill_price must be positive"),
        (Money("100"), Money("-1"), Quantity("10"), "fill_price must be positive"),
        (Money("100"), Money("101"), Quantity("0"), "quantity must be positive"),
        (Money("100"), Money("101"), Quantity("-5"), "quantity must be positive"),
    ],
)
def test_calculate_raises_for_invalid_inputs(
    reference_price: Money,
    fill_price: Money,
    quantity: Quantity,
    expected_message: str,
) -> None:
    service = RealisedSlippageService()

    with pytest.raises(ValueError, match=expected_message):
        service.calculate(
            side=Side.BUY,
            reference_price=reference_price,
            fill_price=fill_price,
            quantity=quantity,
        )
