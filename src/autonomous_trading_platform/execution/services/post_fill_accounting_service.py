from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import (
    CashSnapshot as OrmCashSnapshot,
)
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem as OrmPositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import (
    PositionSnapshot as OrmPositionSnapshot,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


@dataclass
class PostFillAccountingResult:
    realized_pnl: Decimal
    updated_cash: Decimal
    updated_position_count: int


class PostFillAccountingService:
    def __init__(
        self,
        position_ledger_service: PositionLedgerService,
        cash_ledger_service: CashLedgerService,
    ) -> None:
        self.position_ledger_service = position_ledger_service
        self.cash_ledger_service = cash_ledger_service

    def apply_fill(
        self,
        uow: SorUnitOfWork,
        fill: Fill,
        now_utc: datetime,
        market_price: Decimal | None = None,
        commissions: Decimal = Decimal("0"),
        fees: Decimal = Decimal("0"),
    ) -> PostFillAccountingResult:
        latest_position_snapshot = self._get_latest_position_snapshot(uow)
        latest_cash_snapshot = self._get_latest_cash_snapshot(uow)

        existing_positions = (
            list(latest_position_snapshot.positions) if latest_position_snapshot is not None else []
        )
        existing_position = self._find_position(existing_positions, fill.symbol)

        position_result = self.position_ledger_service.apply_fill(
            existing_position=existing_position,
            fill=fill,
            market_price=market_price,
        )
        cash_result = self.cash_ledger_service.apply_fill(
            existing_snapshot=latest_cash_snapshot,
            fill=fill,
            commissions=commissions,
            fees=fees,
        )

        updated_positions = self._replace_position(
            positions=existing_positions,
            symbol=fill.symbol,
            updated_position=position_result.updated_position,
        )

        new_snapshot_id = uuid4()
        new_position_snapshot = OrmPositionSnapshot(
            snapshot_id=new_snapshot_id,
            run_id=fill.run_id,
            timestamp=now_utc,
            source=OrderSource.LEDGER,
        )
        # Do NOT set snapshot_id on items — let SQLAlchemy populate it via the
        # relationship so the upsert can reassign items to an existing snapshot row
        # when multiple fills share the same bar timestamp.
        new_position_snapshot.positions = [
            OrmPositionSnapshotItem(
                symbol=pos.symbol,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                market_price=pos.market_price,
                market_value=pos.market_value,
                unrealized_pnl=pos.unrealized_pnl,
            )
            for pos in updated_positions
        ]
        uow.position_snapshots.upsert(new_position_snapshot)

        currency = latest_cash_snapshot.currency if latest_cash_snapshot is not None else "USD"
        capital_bucket = (
            latest_cash_snapshot.capital_bucket if latest_cash_snapshot is not None else None
        )
        equity = cash_result.cash + sum(
            Decimal(pos.market_value or Decimal("0")) for pos in updated_positions
        )

        new_cash_snapshot = OrmCashSnapshot(
            snapshot_id=uuid4(),
            run_id=fill.run_id,
            timestamp=now_utc,
            currency=currency,
            cash=cash_result.cash,
            buying_power=cash_result.buying_power,
            reserved_cash=cash_result.reserved_cash,
            equity=equity,
            source=OrderSource.LEDGER,
            capital_bucket=capital_bucket,
        )
        uow.cash_snapshots.upsert(new_cash_snapshot)

        return PostFillAccountingResult(
            realized_pnl=position_result.realized_pnl,
            updated_cash=cash_result.cash,
            updated_position_count=len(updated_positions),
        )

    def _find_position(self, positions, symbol: str):
        for position in positions:
            if position.symbol == symbol:
                return position
        return None

    def _replace_position(self, positions, symbol: str, updated_position):
        remaining = [p for p in positions if p.symbol != symbol]
        if updated_position is not None:
            remaining.append(updated_position)
        return remaining

    def _get_latest_position_snapshot(self, uow: SorUnitOfWork):
        if hasattr(uow.position_snapshots, "get_latest"):
            return uow.position_snapshots.get_latest()
        return None

    def _get_latest_cash_snapshot(self, uow: SorUnitOfWork):
        if hasattr(uow.cash_snapshots, "get_latest"):
            return uow.cash_snapshots.get_latest()
        return None
