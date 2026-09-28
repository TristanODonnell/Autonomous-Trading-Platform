from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import (
    UNATTRIBUTED_SLEEVE_ID,
    SleeveEntrySource,
)
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    SleeveAccountingError,
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_T0 = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
_RUN = UUID("00000000-0000-0000-0000-00000000aaaa")


def _fill(
    fill_id: str,
    side: Side,
    quantity: str,
    price: str,
    *,
    symbol: str = "AAPL",
    fees: str | None = None,
    minutes: int = 0,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        broker_order_id=f"order-{fill_id}",
        intent_id=UUID("00000000-0000-0000-0000-00000000bbbb"),
        run_id=_RUN,
        timestamp=_T0 + timedelta(minutes=minutes),
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees) if fees is not None else None,
    )


@pytest.fixture
def uow(db_session: Session) -> SorUnitOfWork:
    return SorUnitOfWork(db_session)


@pytest.fixture
def service() -> StrategySleeveLedgerService:
    return StrategySleeveLedgerService()


class TestFills:
    def test_buy_opens_sleeve_position_and_records_entry(self, uow, service) -> None:
        entry = service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")

        assert entry is not None
        assert entry.source is SleeveEntrySource.BROKER_FILL
        assert entry.realized_pnl == Decimal("0")
        position = service.positions(uow, "A")["AAPL"]
        assert position.quantity == Decimal("10")
        assert position.avg_cost == Decimal("100")

    def test_buys_average_cost_and_sells_realize_pnl(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")
        service.apply_fill(uow, fill=_fill("f2", Side.BUY, "10", "120", minutes=1), strategy_id="A")
        sell = service.apply_fill(
            uow, fill=_fill("f3", Side.SELL, "5", "130", minutes=2), strategy_id="A"
        )

        assert sell is not None
        assert sell.realized_pnl == Decimal("100")  # (130 - 110) * 5
        position = service.positions(uow, "A")["AAPL"]
        assert position.quantity == Decimal("15")
        assert position.avg_cost == Decimal("110")

    def test_selling_to_flat_removes_the_position(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")
        service.apply_fill(uow, fill=_fill("f2", Side.SELL, "10", "90", minutes=1), strategy_id="A")

        assert service.positions(uow, "A") == {}
        realized, _ = uow.strategy_sleeves.realized_totals("A")
        assert realized == Decimal("-100")

    def test_partial_fills_accumulate(self, uow, service) -> None:
        for i, qty in enumerate(("3", "4", "3")):
            service.apply_fill(
                uow, fill=_fill(f"p{i}", Side.BUY, qty, "50", minutes=i), strategy_id="A"
            )

        assert service.positions(uow, "A")["AAPL"].quantity == Decimal("10")
        assert len(uow.strategy_sleeves.get_entries("A")) == 3

    def test_replaying_the_same_fill_is_a_no_op(self, uow, service) -> None:
        fill = _fill("f1", Side.BUY, "10", "100")
        assert service.apply_fill(uow, fill=fill, strategy_id="A") is not None
        assert service.apply_fill(uow, fill=fill, strategy_id="A") is None

        assert service.positions(uow, "A")["AAPL"].quantity == Decimal("10")
        assert len(uow.strategy_sleeves.get_entries("A")) == 1

    def test_sleeves_holding_the_same_symbol_are_independent(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")
        service.apply_fill(uow, fill=_fill("f2", Side.BUY, "5", "200"), strategy_id="B")

        assert service.positions(uow, "A")["AAPL"].avg_cost == Decimal("100")
        assert service.positions(uow, "B")["AAPL"].avg_cost == Decimal("200")
        assert service.aggregate_quantities(uow) == {"AAPL": Decimal("15")}

    def test_overselling_a_sleeve_raises_and_writes_nothing(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "5", "100"), strategy_id="A")

        with pytest.raises(SleeveAccountingError):
            service.apply_fill(
                uow, fill=_fill("f2", Side.SELL, "6", "100", minutes=1), strategy_id="A"
            )
        with pytest.raises(SleeveAccountingError):
            service.apply_fill(uow, fill=_fill("f3", Side.SELL, "1", "100"), strategy_id="B")

        assert service.positions(uow, "A")["AAPL"].quantity == Decimal("5")
        assert len(uow.strategy_sleeves.get_entries("A")) == 1
        assert service.positions(uow, "B") == {}


class TestInternalCross:
    def test_cross_moves_quantity_and_realizes_seller_pnl(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")

        legs = service.apply_internal_cross(
            uow,
            cross_id="x1",
            symbol="AAPL",
            quantity=Decimal("4"),
            price=Decimal("110"),
            buyer_strategy_id="B",
            seller_strategy_id="A",
            timestamp=_T0 + timedelta(minutes=5),
            run_id=_RUN,
        )

        assert legs is not None
        sell_leg, buy_leg = legs
        assert sell_leg.realized_pnl == Decimal("40")
        assert buy_leg.source is SleeveEntrySource.INTERNAL_CROSS
        assert service.positions(uow, "A")["AAPL"].quantity == Decimal("6")
        assert service.positions(uow, "B")["AAPL"].avg_cost == Decimal("110")
        # A cross never changes the account-level total.
        assert service.aggregate_quantities(uow) == {"AAPL": Decimal("10")}

    def test_cross_is_idempotent(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")
        kwargs = dict(
            cross_id="x1",
            symbol="AAPL",
            quantity=Decimal("4"),
            price=Decimal("110"),
            buyer_strategy_id="B",
            seller_strategy_id="A",
            timestamp=_T0,
        )
        assert service.apply_internal_cross(uow, **kwargs) is not None
        assert service.apply_internal_cross(uow, **kwargs) is None
        assert service.positions(uow, "B")["AAPL"].quantity == Decimal("4")

    def test_cross_larger_than_seller_holding_writes_neither_leg(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "3", "100"), strategy_id="A")

        with pytest.raises(SleeveAccountingError):
            service.apply_internal_cross(
                uow,
                cross_id="x1",
                symbol="AAPL",
                quantity=Decimal("4"),
                price=Decimal("110"),
                buyer_strategy_id="B",
                seller_strategy_id="A",
                timestamp=_T0,
            )

        assert service.positions(uow, "A")["AAPL"].quantity == Decimal("3")
        assert service.positions(uow, "B") == {}
        assert uow.strategy_sleeves.get_entries("B") == []

    def test_cross_with_itself_is_rejected(self, uow, service) -> None:
        with pytest.raises(SleeveAccountingError):
            service.apply_internal_cross(
                uow,
                cross_id="x1",
                symbol="AAPL",
                quantity=Decimal("1"),
                price=Decimal("1"),
                buyer_strategy_id="A",
                seller_strategy_id="A",
                timestamp=_T0,
            )


class TestSnapshot:
    def test_snapshot_combines_realized_unrealized_and_fees(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100", fees="1"), strategy_id="A")
        service.apply_fill(
            uow,
            fill=_fill("f2", Side.SELL, "4", "110", fees="0.5", minutes=1),
            strategy_id="A",
        )

        snap = service.snapshot(
            uow,
            strategy_id="A",
            prices={"AAPL": 120.0},
            timestamp=_T0 + timedelta(minutes=2),
            allocated_capital=Decimal("5000"),
        )

        assert snap.realized_pnl == Decimal("40")
        assert snap.unrealized_pnl == Decimal("120")  # 6 * (120 - 100)
        assert snap.fees == Decimal("1.5")
        assert snap.net_pnl == Decimal("158.5")
        assert snap.market_value == Decimal("720")
        assert snap.position_count == 1
        assert uow.strategy_sleeves.get_latest_snapshot("A") is not None

    def test_unpriced_symbols_are_valued_at_cost(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")

        snap = service.snapshot(uow, strategy_id="A", prices={}, timestamp=_T0)

        assert snap.unpriced_symbols == ["AAPL"]
        assert snap.market_value == Decimal("1000")
        assert snap.unrealized_pnl == Decimal("0")


class TestReconcile:
    def test_balanced_when_sleeves_sum_to_account(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")
        service.apply_fill(uow, fill=_fill("f2", Side.BUY, "5", "100"), strategy_id="B")

        report = service.reconcile(uow, account_positions={"AAPL": Decimal("15")}, timestamp=_T0)

        assert report.is_balanced

    def test_unowned_account_shares_are_reported(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")

        report = service.reconcile(
            uow,
            account_positions={"AAPL": Decimal("12"), "MSFT": Decimal("3")},
            timestamp=_T0,
        )

        assert not report.is_balanced
        diffs = {m.symbol: m.difference for m in report.mismatches}
        assert diffs == {"AAPL": Decimal("2"), "MSFT": Decimal("3")}

    def test_adopt_unowned_assigns_them_to_the_unattributed_sleeve(self, uow, service) -> None:
        account = {
            "MSFT": Position(symbol="MSFT", quantity=Decimal("3"), avg_cost=Decimal("400")),
        }

        report = service.reconcile(
            uow, account_positions=account, timestamp=_T0, adopt_unowned=True
        )

        assert report.is_balanced
        assert report.adopted_symbols == ["MSFT"]
        held = service.positions(uow, UNATTRIBUTED_SLEEVE_ID)["MSFT"]
        assert held.quantity == Decimal("3")
        assert held.avg_cost == Decimal("400")
        again = service.reconcile(uow, account_positions=account, timestamp=_T0)
        assert again.is_balanced

    def test_sleeve_over_claim_is_reported_never_adopted(self, uow, service) -> None:
        service.apply_fill(uow, fill=_fill("f1", Side.BUY, "10", "100"), strategy_id="A")

        report = service.reconcile(
            uow,
            account_positions={
                "AAPL": Position(symbol="AAPL", quantity=Decimal("8"), avg_cost=Decimal("100"))
            },
            timestamp=_T0,
            adopt_unowned=True,
        )

        assert [m.difference for m in report.mismatches] == [Decimal("-2")]
        assert report.adopted_symbols == []
        assert service.positions(uow, UNATTRIBUTED_SLEEVE_ID) == {}
