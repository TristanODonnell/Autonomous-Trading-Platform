"""Finding 25: a ledger resync must not double-count fills the next tick will apply."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.execution.services.broker_runtime_sync_service import (
    BrokerRuntimeSyncService,
)
from autonomous_trading_platform.storage.sor.models.broker_account_snapshots import (
    BrokerAccountSnapshot,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder
from tests.execution.test_broker_runtime_sync_service import (
    FakeBrokerClient,
    _order_payload,
    _settings,
)


def _open_tracked_order(
    db_session: Session,
    *,
    broker_order_id: str,
    symbol: str,
    previous_filled_qty: str = "0",
) -> None:
    db_session.add(
        TrackedOrder(
            order_id=uuid4(),
            intent_id=uuid4(),
            run_id=uuid4(),
            strategy_id="momentum_v1",
            account_id="paper-account-1",
            symbol=symbol,
            broker_order_id=broker_order_id,
            current_status=OrderStatus.SUBMITTED,
            previous_filled_qty=Decimal(previous_filled_qty),
            previous_avg_fill_price=None,
            is_open=True,
        )
    )
    db_session.commit()


def _pending_fill_client(positions: list[dict] | None = None) -> FakeBrokerClient:
    """Broker that filled AAPL buy 3 @ 331.81 and TLT sell 2 @ 81.42 after our last tick."""
    return FakeBrokerClient(
        positions=positions,
        orders={
            "aapl-buy": _order_payload(
                id="aapl-buy",
                symbol="AAPL",
                side="buy",
                qty="3",
                status="filled",
                filled_qty="3",
                filled_avg_price="331.81",
                updated_at="2026-11-03T20:56:29Z",
            ),
            "tlt-sell": _order_payload(
                id="tlt-sell",
                symbol="TLT",
                side="sell",
                qty="2",
                status="filled",
                filled_qty="2",
                filled_avg_price="81.42",
                updated_at="2026-11-04T14:36:39Z",
            ),
            "msft-open": _order_payload(
                id="msft-open",
                symbol="MSFT",
                side="buy",
                qty="1",
                status="accepted",
                filled_qty="0",
                filled_avg_price=None,
            ),
        },
    )


def _service(db_session: Session, broker_client: FakeBrokerClient) -> BrokerRuntimeSyncService:
    return BrokerRuntimeSyncService(
        session=db_session, broker_client=broker_client, settings=_settings()
    )


def test_pending_fills_is_empty_without_open_tracked_orders(db_session: Session) -> None:
    assert _service(db_session, _pending_fill_client()).pending_fills() == ()


def test_pending_fills_lists_only_filled_quantity_not_yet_applied(db_session: Session) -> None:
    _open_tracked_order(db_session, broker_order_id="aapl-buy", symbol="AAPL")
    _open_tracked_order(db_session, broker_order_id="tlt-sell", symbol="TLT")
    _open_tracked_order(db_session, broker_order_id="msft-open", symbol="MSFT")

    pending = _service(db_session, _pending_fill_client()).pending_fills()

    assert {(fill.symbol, fill.side, fill.quantity, fill.price) for fill in pending} == {
        ("AAPL", "buy", Decimal("3"), Decimal("331.81")),
        ("TLT", "sell", Decimal("2"), Decimal("81.42")),
    }
    assert sum(fill.cash_delta for fill in pending) == Decimal("995.43") - Decimal("162.84")


def test_sync_broker_runtime_state_holds_back_pending_fills_from_cash(
    db_session: Session,
) -> None:
    _open_tracked_order(db_session, broker_order_id="aapl-buy", symbol="AAPL")
    _open_tracked_order(db_session, broker_order_id="tlt-sell", symbol="TLT")
    service = _service(db_session, _pending_fill_client())

    result = service.sync_broker_runtime_state(run_id=uuid4())

    cash_snapshot = db_session.get(CashSnapshot, result.cash_snapshot_id)
    assert cash_snapshot is not None
    # Broker cash is already net of the buy (-995.43) and the sell (+162.84); the ledger
    # gets those back so that applying the fills on the next tick lands on the broker.
    assert cash_snapshot.cash == Decimal("1000.25") + Decimal("995.43") - Decimal("162.84")
    assert cash_snapshot.buying_power == Decimal("2000.50") + Decimal("832.59")
    assert cash_snapshot.equity == Decimal("3000.75")
    assert len(result.pending_fills) == 2
    # The broker account snapshot itself stays the raw broker view.
    account_snapshot = db_session.get(BrokerAccountSnapshot, result.account_snapshot_id)
    assert account_snapshot is not None
    assert account_snapshot.cash == Decimal("1000.25")


def test_sync_positions_from_broker_nets_out_pending_fills(db_session: Session) -> None:
    _open_tracked_order(db_session, broker_order_id="aapl-buy", symbol="AAPL")
    _open_tracked_order(db_session, broker_order_id="tlt-sell", symbol="TLT")
    broker_client = _pending_fill_client(
        positions=[
            # Held only because of the pending buy: must not appear in the ledger yet.
            {"symbol": "AAPL", "qty": "3", "avg_entry_price": "331.81", "current_price": "332"},
            # Untouched by any pending fill: copied as-is.
            {
                "symbol": "MSFT",
                "qty": "5",
                "avg_entry_price": "500",
                "current_price": "510",
                "market_value": "2550",
                "unrealized_pl": "50",
            },
            # TLT is flat at the broker because the pending sell already went through.
        ]
    )

    snapshot = _service(db_session, broker_client).sync_positions_from_broker(run_id=uuid4())

    by_symbol = {item.symbol: item for item in snapshot.positions}
    assert set(by_symbol) == {"MSFT", "TLT"}
    assert by_symbol["MSFT"].quantity == Decimal("5")
    assert by_symbol["MSFT"].market_value == Decimal("2550")
    # The ledger still holds the 2 TLT until the tick applies the sell, at the fill price.
    assert by_symbol["TLT"].quantity == Decimal("2")
    assert by_symbol["TLT"].avg_cost == Decimal("81.42")
    assert by_symbol["TLT"].market_value == Decimal("162.84")


def test_sync_positions_from_broker_partially_nets_a_held_position(db_session: Session) -> None:
    _open_tracked_order(db_session, broker_order_id="aapl-buy", symbol="AAPL")
    broker_client = _pending_fill_client(
        positions=[
            {"symbol": "AAPL", "qty": "7", "avg_entry_price": "330", "current_price": "340"},
        ]
    )

    snapshot = _service(db_session, broker_client).sync_positions_from_broker(run_id=uuid4())

    (item,) = snapshot.positions
    assert item.symbol == "AAPL"
    assert item.quantity == Decimal("4")
    assert item.market_value == Decimal("1360")
    assert item.unrealized_pnl == Decimal("40")


def test_validate_broker_runtime_consistency_is_ok_after_resync_with_pending_fills(
    db_session: Session,
) -> None:
    _open_tracked_order(db_session, broker_order_id="aapl-buy", symbol="AAPL")
    service = _service(db_session, _pending_fill_client())
    service.sync_broker_runtime_state(run_id=uuid4())

    result = service.validate_broker_runtime_consistency()

    assert result.ok, result.mismatches
    assert len(result.pending_fills) == 1


def test_validate_broker_runtime_consistency_flags_ledger_that_ignored_pending_fills(
    db_session: Session,
) -> None:
    # A cash row equal to the raw broker cash is wrong while a fill is pending: the next
    # tick subtracts the buy again (this is exactly how Finding 25 happened).
    _open_tracked_order(db_session, broker_order_id="aapl-buy", symbol="AAPL")
    service = _service(db_session, _pending_fill_client())
    service.sync_account_snapshot()
    service.sync_cash_snapshot_from_account(run_id=uuid4())

    result = service.validate_broker_runtime_consistency()

    assert not result.ok
    assert "cash.cash" in result.mismatches
