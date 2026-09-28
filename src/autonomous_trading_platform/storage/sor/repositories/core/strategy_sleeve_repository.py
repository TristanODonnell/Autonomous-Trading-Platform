from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    StrategySleeveLedgerRow,
    StrategySleevePositionRow,
    StrategySleeveSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class StrategySleeveRepository(BaseRepository):
    """Persistence for strategy sleeves: current positions, ledger entries, snapshots."""

    # -----------------------------
    # Positions
    # -----------------------------

    def get_position(self, strategy_id: str, symbol: str) -> StrategySleevePositionRow | None:
        row: StrategySleevePositionRow | None = self.session.get(
            StrategySleevePositionRow, (strategy_id, symbol)
        )
        return row

    def get_positions(self, strategy_id: str) -> list[StrategySleevePositionRow]:
        return list(
            self.session.scalars(
                select(StrategySleevePositionRow)
                .where(StrategySleevePositionRow.strategy_id == strategy_id)
                .order_by(StrategySleevePositionRow.symbol)
            ).all()
        )

    def get_all_positions(self) -> list[StrategySleevePositionRow]:
        return list(
            self.session.scalars(
                select(StrategySleevePositionRow).order_by(
                    StrategySleevePositionRow.strategy_id, StrategySleevePositionRow.symbol
                )
            ).all()
        )

    def save_position(self, row: StrategySleevePositionRow) -> None:
        existing = self.get_position(row.strategy_id, row.symbol)
        if existing is None:
            self.session.add(row)
        else:
            existing.quantity = row.quantity
            existing.avg_cost = row.avg_cost
            existing.updated_at = row.updated_at
        self.session.flush()

    def delete_position(self, strategy_id: str, symbol: str) -> None:
        existing = self.get_position(strategy_id, symbol)
        if existing is not None:
            self.session.delete(existing)
            self.session.flush()

    # -----------------------------
    # Ledger
    # -----------------------------

    def has_entry(self, entry_id: str) -> bool:
        return self.session.get(StrategySleeveLedgerRow, entry_id) is not None

    def has_any_entries(self) -> bool:
        return (
            self.session.scalars(select(StrategySleeveLedgerRow.entry_id).limit(1)).first()
            is not None
        )

    def insert_entry(self, row: StrategySleeveLedgerRow) -> None:
        self.session.add(row)
        self.session.flush()

    def get_entries(self, strategy_id: str) -> list[StrategySleeveLedgerRow]:
        return list(
            self.session.scalars(
                select(StrategySleeveLedgerRow)
                .where(StrategySleeveLedgerRow.strategy_id == strategy_id)
                .order_by(StrategySleeveLedgerRow.timestamp, StrategySleeveLedgerRow.entry_id)
            ).all()
        )

    def realized_totals(self, strategy_id: str) -> tuple[Decimal, Decimal]:
        """Return (cumulative realized P&L, cumulative fees) for a sleeve."""
        realized, fees = self.session.execute(
            select(
                func.coalesce(func.sum(StrategySleeveLedgerRow.realized_pnl), 0),
                func.coalesce(func.sum(StrategySleeveLedgerRow.fees), 0),
            ).where(StrategySleeveLedgerRow.strategy_id == strategy_id)
        ).one()
        return Decimal(str(realized)), Decimal(str(fees))

    # -----------------------------
    # Snapshots
    # -----------------------------

    def insert_snapshot(self, row: StrategySleeveSnapshotRow) -> None:
        self.session.add(row)
        self.session.flush()

    def get_snapshots(self, strategy_id: str) -> list[StrategySleeveSnapshotRow]:
        return list(
            self.session.scalars(
                select(StrategySleeveSnapshotRow)
                .where(StrategySleeveSnapshotRow.strategy_id == strategy_id)
                .order_by(StrategySleeveSnapshotRow.timestamp)
            ).all()
        )

    def get_latest_snapshot(self, strategy_id: str) -> StrategySleeveSnapshotRow | None:
        row: StrategySleeveSnapshotRow | None = self.session.scalars(
            select(StrategySleeveSnapshotRow)
            .where(StrategySleeveSnapshotRow.strategy_id == strategy_id)
            .order_by(StrategySleeveSnapshotRow.timestamp.desc())
            .limit(1)
        ).one_or_none()
        return row
