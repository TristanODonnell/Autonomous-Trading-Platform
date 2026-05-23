from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class TrackedOrderRepository(BaseRepository):
    def get_by_order_id(self, order_id: UUID) -> TrackedOrder | None:
        stmt = select(TrackedOrder).where(TrackedOrder.order_id == order_id)
        return cast(
            TrackedOrder | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def list_open_orders(self) -> list[TrackedOrder]:
        stmt = select(TrackedOrder).where(TrackedOrder.is_open.is_(True))
        return cast(list[TrackedOrder], list(self.session.execute(stmt).scalars().all()))

    def list_open_orders_for_run(self, run_id: UUID) -> list[TrackedOrder]:
        stmt = select(TrackedOrder).where(
            TrackedOrder.run_id == run_id,
            TrackedOrder.is_open.is_(True),
        )
        return cast(list[TrackedOrder], list(self.session.execute(stmt).scalars().all()))

    def list_reconcilable_orders(self) -> list[TrackedOrder]:
        stmt = select(TrackedOrder).where(
            TrackedOrder.current_status.in_(
                [
                    OrderStatus.PENDING_NEW,
                    OrderStatus.SUBMITTED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.PENDING_CANCEL,
                ]
            )
        )
        return cast(list[TrackedOrder], list(self.session.execute(stmt).scalars().all()))

    def upsert(self, row: TrackedOrder) -> TrackedOrder:
        existing = self.get_by_order_id(row.order_id)
        if existing is None:
            self.session.add(row)
            return row

        for column in TrackedOrder.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))
        return existing

    def mark_closed(
        self,
        order_id: UUID,
        *,
        status: OrderStatus,
        updated_at: datetime,
    ) -> None:
        existing = self.get_by_order_id(order_id)
        if existing is None:
            return
        existing.current_status = status
        existing.is_open = False
        existing.updated_at = updated_at
