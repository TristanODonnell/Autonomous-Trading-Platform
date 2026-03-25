from decimal import Decimal

import pytest

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from tests.utilities.factories import make_fill


@pytest.fixture
def service() -> PositionLedgerService:
    return PositionLedgerService()


def make_position(
    *,
    quantity: str,
    avg_cost: str,
    market_price: str = "100",
    symbol: str = "AAPL",
) -> Position:
    qty = Decimal(quantity)
    avg = Decimal(avg_cost)
    mark = Decimal(market_price)

    return Position(
        symbol=symbol,
        quantity=qty,
        avg_cost=avg,
        market_price=mark,
        market_value=qty * mark,
        unrealized_pnl=(mark - avg) * qty,
    )


class TestPositionLedgerService:
    def test_buy_with_no_existing_position_creates_new_position(
        self,
        service: PositionLedgerService,
    ) -> None:
        fill = make_fill(side=Side.BUY, quantity="10", price="100")

        result = service.apply_fill(existing_position=None, fill=fill)

        assert result.updated_position is not None
        assert result.updated_position.symbol == "AAPL"
        assert result.updated_position.quantity == Decimal("10")
        assert result.updated_position.avg_cost == Decimal("100")
        assert result.updated_position.market_price == Decimal("100")
        assert result.updated_position.market_value == Decimal("1000")
        assert result.updated_position.unrealized_pnl == Decimal("0")
        assert result.realized_pnl == Decimal("0")
        assert result.closed_quantity == Decimal("0")

    def test_buy_increments_position_and_recalculates_average_cost(
        self,
        service: PositionLedgerService,
    ) -> None:
        existing_position = make_position(
            quantity="10",
            avg_cost="100",
            market_price="100",
        )
        fill = make_fill(side=Side.BUY, quantity="5", price="130")

        result = service.apply_fill(
            existing_position=existing_position,
            fill=fill,
        )

        assert result.updated_position is not None
        assert result.updated_position.quantity == Decimal("15")
        assert result.updated_position.avg_cost == Decimal("110")
        assert result.updated_position.market_price == Decimal("130")
        assert result.updated_position.market_value == Decimal("1950")
        assert result.updated_position.unrealized_pnl == Decimal("300")
        assert result.realized_pnl == Decimal("0")
        assert result.closed_quantity == Decimal("0")

    def test_sell_decrements_position_and_calculates_realized_pnl(
        self,
        service: PositionLedgerService,
    ) -> None:
        existing_position = make_position(
            quantity="10",
            avg_cost="100",
            market_price="100",
        )
        fill = make_fill(side=Side.SELL, quantity="4", price="130")

        result = service.apply_fill(
            existing_position=existing_position,
            fill=fill,
        )

        assert result.updated_position is not None
        assert result.updated_position.quantity == Decimal("6")
        assert result.updated_position.avg_cost == Decimal("100")
        assert result.updated_position.market_price == Decimal("130")
        assert result.updated_position.market_value == Decimal("780")
        assert result.updated_position.unrealized_pnl == Decimal("180")
        assert result.realized_pnl == Decimal("120")
        assert result.closed_quantity == Decimal("4")

    def test_sell_full_quantity_closes_position(
        self,
        service: PositionLedgerService,
    ) -> None:
        existing_position = make_position(
            quantity="10",
            avg_cost="100",
            market_price="100",
        )
        fill = make_fill(side=Side.SELL, quantity="10", price="90")

        result = service.apply_fill(
            existing_position=existing_position,
            fill=fill,
        )

        assert result.updated_position is None
        assert result.realized_pnl == Decimal("-100")
        assert result.closed_quantity == Decimal("10")

    def test_multiple_buys_update_average_cost_deterministically(
        self,
        service: PositionLedgerService,
    ) -> None:
        first_fill = make_fill(side=Side.BUY, quantity="10", price="100")
        first_result = service.apply_fill(existing_position=None, fill=first_fill)

        second_fill = make_fill(side=Side.BUY, quantity="10", price="120")
        second_result = service.apply_fill(
            existing_position=first_result.updated_position,
            fill=second_fill,
        )

        third_fill = make_fill(side=Side.BUY, quantity="5", price="110")
        third_result = service.apply_fill(
            existing_position=second_result.updated_position,
            fill=third_fill,
        )

        assert third_result.updated_position is not None
        assert third_result.updated_position.quantity == Decimal("25")
        assert third_result.updated_position.avg_cost == Decimal("110")
        assert third_result.realized_pnl == Decimal("0")
        assert third_result.closed_quantity == Decimal("0")

    def test_market_price_argument_overrides_fill_price_for_marking(
        self,
        service: PositionLedgerService,
    ) -> None:
        existing_position = make_position(
            quantity="10",
            avg_cost="100",
            market_price="100",
        )
        fill = make_fill(side=Side.SELL, quantity="4", price="130")

        result = service.apply_fill(
            existing_position=existing_position,
            fill=fill,
            market_price=Decimal("125"),
        )

        assert result.updated_position is not None
        assert result.updated_position.quantity == Decimal("6")
        assert result.updated_position.avg_cost == Decimal("100")
        assert result.updated_position.market_price == Decimal("125")
        assert result.updated_position.market_value == Decimal("750")
        assert result.updated_position.unrealized_pnl == Decimal("150")
        assert result.realized_pnl == Decimal("120")

    def test_sell_more_than_available_quantity_raises_error(
        self,
        service: PositionLedgerService,
    ) -> None:
        existing_position = make_position(
            quantity="10",
            avg_cost="100",
            market_price="100",
        )
        fill = make_fill(side=Side.SELL, quantity="11", price="100")

        with pytest.raises(ValueError, match="selling through zero is not supported in v1"):
            service.apply_fill(
                existing_position=existing_position,
                fill=fill,
            )

    def test_sell_without_existing_position_raises_error(
        self,
        service: PositionLedgerService,
    ) -> None:
        fill = make_fill(side=Side.SELL, quantity="5", price="100")

        with pytest.raises(
            ValueError,
            match="cannot sell without an existing long position in v1",
        ):
            service.apply_fill(existing_position=None, fill=fill)

    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_apply_fill_rejects_non_positive_quantity(
        self,
        service: PositionLedgerService,
        quantity: str,
    ) -> None:
        fill = make_fill(side=Side.BUY, quantity=quantity, price="100")

        with pytest.raises(ValueError, match="fill.quantity must be positive"):
            service.apply_fill(existing_position=None, fill=fill)

    @pytest.mark.parametrize("price", ["0", "-1"])
    def test_apply_fill_rejects_non_positive_price(
        self,
        service: PositionLedgerService,
        price: str,
    ) -> None:
        fill = make_fill(side=Side.BUY, quantity="10", price=price)

        with pytest.raises(ValueError, match="fill.price must be positive"):
            service.apply_fill(existing_position=None, fill=fill)
