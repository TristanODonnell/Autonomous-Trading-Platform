from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.common.enums import OrderSource, Side
from autonomous_trading_platform.execution.services.cash_ledger_service import (
    CashLedgerService,
)
from tests.utilities.factories import make_fill


@pytest.fixture
def service() -> CashLedgerService:
    return CashLedgerService()


def make_snapshot(
    *,
    cash: str = "10000",
    buying_power: str | None = None,
    reserved_cash: str = "0",
    equity: str | None = None,
    capital_bucket: str | None = None,
) -> CashSnapshot:
    resolved_buying_power = buying_power if buying_power is not None else cash

    return CashSnapshot(
        snapshot_id=UUID("00000000-0000-0000-0000-000000000001"),
        run_id=UUID("00000000-0000-0000-0000-000000000002"),
        timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
        currency="USD",
        cash=Decimal(cash),
        buying_power=Decimal(resolved_buying_power),
        reserved_cash=Decimal(reserved_cash),
        equity=Decimal(equity) if equity is not None else None,
        source=OrderSource.LEDGER,
        capital_bucket=Decimal(capital_bucket) if capital_bucket is not None else None,
    )


class TestCashLedgerService:
    def test_buy_reduces_cash_buying_power_and_reserved_cash(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="10000", reserved_cash="1500")
        fill = make_fill(side=Side.BUY, quantity="10", price="100")

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
        )

        assert result.cash == Decimal("9000")
        assert result.buying_power == Decimal("9000")
        assert result.reserved_cash == Decimal("500")

    def test_sell_increases_cash_buying_power_and_reduces_reserved_cash(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="5000", reserved_cash="300")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
        )

        assert result.cash == Decimal("6000")
        assert result.buying_power == Decimal("6000")
        assert result.reserved_cash == Decimal("0")

    def test_buy_fees_and_commissions_reduce_cash_appropriately(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="10000", reserved_cash="0")
        fill = make_fill(side=Side.BUY, quantity="10", price="100")

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
            commissions=Decimal("1.25"),
            fees=Decimal("0.75"),
        )

        assert result.cash == Decimal("8998.00")
        assert result.buying_power == Decimal("8998.00")
        assert result.total_costs == Decimal("2.00")

    def test_sell_fees_and_commissions_reduce_proceeds_appropriately(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="5000", reserved_cash="0")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
            commissions=Decimal("1.25"),
            fees=Decimal("0.75"),
        )

        assert result.cash == Decimal("5998.00")
        assert result.buying_power == Decimal("5998.00")
        assert result.total_costs == Decimal("2.00")

    def test_apply_fill_uses_zero_starting_cash_when_snapshot_is_none(
        self,
        service: CashLedgerService,
    ) -> None:
        fill = make_fill(side=Side.SELL, quantity="2", price="100")

        result = service.apply_fill(
            existing_snapshot=None,
            fill=fill,
        )

        assert result.cash == Decimal("200")
        assert result.buying_power == Decimal("200")
        assert result.reserved_cash == Decimal("0")
        assert result.total_costs == Decimal("0")

    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_apply_fill_rejects_non_positive_quantity(
        self,
        service: CashLedgerService,
        quantity: str,
    ) -> None:
        snapshot = make_snapshot()
        fill = make_fill(side=Side.BUY, quantity=quantity, price="100")

        with pytest.raises(ValueError, match="fill.quantity must be positive"):
            service.apply_fill(existing_snapshot=snapshot, fill=fill)

    @pytest.mark.parametrize("price", ["0", "-1"])
    def test_apply_fill_rejects_non_positive_price(
        self,
        service: CashLedgerService,
        price: str,
    ) -> None:
        snapshot = make_snapshot()
        fill = make_fill(side=Side.BUY, quantity="10", price=price)

        with pytest.raises(ValueError, match="fill.price must be positive"):
            service.apply_fill(existing_snapshot=snapshot, fill=fill)

    def test_apply_fill_rejects_negative_commissions(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot()
        fill = make_fill(side=Side.BUY)

        with pytest.raises(ValueError, match="commissions cannot be negative"):
            service.apply_fill(
                existing_snapshot=snapshot,
                fill=fill,
                commissions=Decimal("-0.01"),
            )

    def test_apply_fill_rejects_negative_fees(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot()
        fill = make_fill(side=Side.BUY)

        with pytest.raises(ValueError, match="fees cannot be negative"):
            service.apply_fill(
                existing_snapshot=snapshot,
                fill=fill,
                fees=Decimal("-0.01"),
            )

    def test_buy_reduces_reserved_cash(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="10000", reserved_cash="1000")
        fill = make_fill(side=Side.BUY, quantity="5", price="100")

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
        )

        assert result.cash == Decimal("9500")
        assert result.reserved_cash == Decimal("500")

    def test_sell_reduces_reserved_cash(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="5000", reserved_cash="400")
        fill = make_fill(side=Side.SELL, quantity="2", price="100")

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
        )

        assert result.cash == Decimal("5200")
        assert result.reserved_cash == Decimal("200")

    def test_reserved_cash_does_not_go_negative(
        self,
        service: CashLedgerService,
    ) -> None:
        snapshot = make_snapshot(cash="5000", reserved_cash="100")
        fill = make_fill(side=Side.BUY, quantity="2", price="100")  # gross = 200

        result = service.apply_fill(
            existing_snapshot=snapshot,
            fill=fill,
        )

        assert result.reserved_cash == Decimal("0")
