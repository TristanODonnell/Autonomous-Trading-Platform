from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import OrderSource, Side
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.execution.services.post_fill_accounting_service import (
    PostFillAccountingService,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_RUN = UUID("00000000-0000-0000-0000-00000000dddd")
_BAR = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def _fill(fill_id: str, symbol: str, quantity: str, price: str, side: Side = Side.BUY) -> Fill:
    return Fill(
        fill_id=fill_id,
        broker_order_id=f"order-{fill_id}",
        intent_id=uuid4(),
        run_id=_RUN,
        timestamp=_BAR,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


def _seed_cash(session: Session, cash: str) -> None:
    session.add(
        CashSnapshot(
            snapshot_id=uuid4(),
            run_id=_RUN,
            timestamp=_BAR - timedelta(days=1),
            currency="USD",
            cash=Decimal(cash),
            buying_power=Decimal(cash),
            reserved_cash=Decimal("0"),
            equity=Decimal(cash),
            source=OrderSource.LEDGER,
        )
    )
    session.flush()


def _service() -> PostFillAccountingService:
    return PostFillAccountingService(
        position_ledger_service=PositionLedgerService(),
        cash_ledger_service=CashLedgerService(),
    )


def test_fills_in_the_same_bar_chain_on_one_cash_row(db_session: Session) -> None:
    _seed_cash(db_session, "100000")
    service = _service()

    for fill in (
        _fill("f1", "AAPL", "10", "100"),
        _fill("f2", "MSFT", "5", "400"),
        _fill("f3", "SPY", "2", "500"),
    ):
        with SorUnitOfWork(db_session) as uow:
            service.apply_fill(uow=uow, fill=fill, now_utc=_BAR)

    bar_rows = db_session.query(CashSnapshot).filter(CashSnapshot.timestamp == _BAR).all()
    assert len(bar_rows) == 1
    # 100000 - 1000 - 2000 - 1000
    assert bar_rows[0].cash == Decimal("96000")


def test_a_later_bar_starts_a_new_cash_row_from_the_previous_balance(
    db_session: Session,
) -> None:
    _seed_cash(db_session, "100000")
    service = _service()
    with SorUnitOfWork(db_session) as uow:
        service.apply_fill(uow=uow, fill=_fill("f1", "AAPL", "10", "100"), now_utc=_BAR)

    next_bar = _BAR + timedelta(days=1)
    with SorUnitOfWork(db_session) as uow:
        service.apply_fill(
            uow=uow, fill=_fill("f2", "AAPL", "10", "110", side=Side.SELL), now_utc=next_bar
        )

    rows = (
        db_session.query(CashSnapshot)
        .filter(CashSnapshot.timestamp.in_([_BAR, next_bar]))
        .order_by(CashSnapshot.timestamp)
        .all()
    )
    assert [row.cash for row in rows] == [Decimal("99000"), Decimal("100100")]
