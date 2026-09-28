from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    ShadowSleeveLedgerRow,
    ShadowSleevePositionRow,
    ShadowSleeveSnapshotRow,
    SleeveLedgerBase,
    SleevePositionBase,
    SleeveSnapshotBase,
    StrategySleeveLedgerRow,
    StrategySleevePositionRow,
    StrategySleeveSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class StrategySleeveRepository(BaseRepository):
    """Persistence for strategy sleeves: current positions, ledger entries, snapshots.

    Operates on the real sleeve tables; ShadowSleeveRepository has the same
    interface over the shadow (on-deck) tables.
    """

    position_model: type[SleevePositionBase] = StrategySleevePositionRow
    ledger_model: type[SleeveLedgerBase] = StrategySleeveLedgerRow
    snapshot_model: type[SleeveSnapshotBase] = StrategySleeveSnapshotRow

    # -----------------------------
    # Positions
    # -----------------------------

    def get_position(self, strategy_id: str, symbol: str) -> SleevePositionBase | None:
        row: SleevePositionBase | None = self.session.get(
            self.position_model, (strategy_id, symbol)
        )
        return row

    def get_positions(self, strategy_id: str) -> list[SleevePositionBase]:
        model = self.position_model
        return list(
            self.session.scalars(
                select(model).where(model.strategy_id == strategy_id).order_by(model.symbol)
            ).all()
        )

    def get_all_positions(self) -> list[SleevePositionBase]:
        model = self.position_model
        return list(
            self.session.scalars(select(model).order_by(model.strategy_id, model.symbol)).all()
        )

    def save_position(self, row: SleevePositionBase) -> None:
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
        return self.session.get(self.ledger_model, entry_id) is not None

    def has_any_entries(self) -> bool:
        model = self.ledger_model
        return self.session.scalars(select(model.entry_id).limit(1)).first() is not None

    def insert_entry(self, row: SleeveLedgerBase) -> None:
        self.session.add(row)
        self.session.flush()

    def get_entries(self, strategy_id: str) -> list[SleeveLedgerBase]:
        model = self.ledger_model
        return list(
            self.session.scalars(
                select(model)
                .where(model.strategy_id == strategy_id)
                .order_by(model.timestamp, model.entry_id)
            ).all()
        )

    def realized_totals(self, strategy_id: str) -> tuple[Decimal, Decimal]:
        """Return (cumulative realized P&L, cumulative fees) for a sleeve."""
        model = self.ledger_model
        realized, fees = self.session.execute(
            select(
                func.coalesce(func.sum(model.realized_pnl), 0),
                func.coalesce(func.sum(model.fees), 0),
            ).where(model.strategy_id == strategy_id)
        ).one()
        return Decimal(str(realized)), Decimal(str(fees))

    # -----------------------------
    # Snapshots
    # -----------------------------

    def insert_snapshot(self, row: SleeveSnapshotBase) -> None:
        self.session.add(row)
        self.session.flush()

    def get_snapshots(self, strategy_id: str) -> list[SleeveSnapshotBase]:
        model = self.snapshot_model
        return list(
            self.session.scalars(
                select(model).where(model.strategy_id == strategy_id).order_by(model.timestamp)
            ).all()
        )

    def get_latest_snapshot(self, strategy_id: str) -> SleeveSnapshotBase | None:
        model = self.snapshot_model
        row: SleeveSnapshotBase | None = self.session.scalars(
            select(model)
            .where(model.strategy_id == strategy_id)
            .order_by(model.timestamp.desc())
            .limit(1)
        ).one_or_none()
        return row


class ShadowSleeveRepository(StrategySleeveRepository):
    """Shadow (on-deck) sleeves: simulated fills only, never part of the account."""

    position_model = ShadowSleevePositionRow
    ledger_model = ShadowSleeveLedgerRow
    snapshot_model = ShadowSleeveSnapshotRow
