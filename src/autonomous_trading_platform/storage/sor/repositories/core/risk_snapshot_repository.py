from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from autonomous_trading_platform.contracts.accounting.risk_snapshot import (
    RiskSnapshot as RiskSnapshotContract,
)
from autonomous_trading_platform.storage.sor.models.risk_snapshots import (
    RiskSnapshot as RiskSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


class RiskSnapshotRepository(BaseRepository):
    def _to_row(self, row: RiskSnapshotContract) -> RiskSnapshotRow:
        breach_gross = getattr(row, "breach_gross_exposure", False)
        breach_net = getattr(row, "breach_net_exposure", False)
        breach_leverage = getattr(row, "breach_leverage", False)

        block_reasons: list[str] = []
        if breach_gross:
            block_reasons.append("gross_exposure")
        if breach_net:
            block_reasons.append("net_exposure")
        if breach_leverage:
            block_reasons.append("leverage")

        is_blocked = getattr(
            row,
            "is_blocked",
            breach_gross or breach_net or breach_leverage,
        )

        drawdown_pct = getattr(row, "drawdown_pct", None)

        limits = _json_safe(getattr(row, "limits", {}))
        utilization = _json_safe(getattr(row, "utilization", {}))

        return RiskSnapshotRow(
            snapshot_id=row.snapshot_id,
            run_id=row.run_id,
            timestamp=row.timestamp,
            gross_exposure=row.gross_exposure,
            net_exposure=row.net_exposure,
            leverage=row.leverage,
            drawdown_pct=drawdown_pct,
            limits=limits,
            utilization=utilization,
            is_blocked=is_blocked,
            block_reasons=block_reasons or None,
        )

    def get_by_snapshot_id(self, id_value: UUID) -> RiskSnapshotRow | None:
        stmt = select(RiskSnapshotRow).where(RiskSnapshotRow.snapshot_id == id_value)
        result: RiskSnapshotRow | None = self.session.execute(stmt).scalar_one_or_none()
        return result

    def insert(self, row: RiskSnapshotContract) -> None:
        orm_row = self._to_row(row)
        self.session.add(orm_row)

    def insert_many(self, rows: list[RiskSnapshotContract]) -> None:
        orm_rows = [self._to_row(row) for row in rows]
        self.session.add_all(orm_rows)

    def upsert(self, row: RiskSnapshotContract) -> RiskSnapshotRow:
        existing = self.get_by_snapshot_id(row.snapshot_id)

        if existing is None:
            orm_row = self._to_row(row)
            self.session.add(orm_row)
            self.session.flush()
            return orm_row

        breach_gross = getattr(row, "breach_gross_exposure", False)
        breach_net = getattr(row, "breach_net_exposure", False)
        breach_leverage = getattr(row, "breach_leverage", False)

        block_reasons: list[str] = []
        if breach_gross:
            block_reasons.append("gross_exposure")
        if breach_net:
            block_reasons.append("net_exposure")
        if breach_leverage:
            block_reasons.append("leverage")

        existing.run_id = row.run_id
        existing.timestamp = row.timestamp
        existing.gross_exposure = row.gross_exposure
        existing.net_exposure = row.net_exposure
        existing.leverage = row.leverage
        existing.drawdown_pct = getattr(row, "drawdown_pct", None)

        existing.limits = _json_safe(getattr(row, "limits", {}))
        existing.utilization = _json_safe(getattr(row, "utilization", {}))

        existing.is_blocked = getattr(
            row,
            "is_blocked",
            breach_gross or breach_net or breach_leverage,
        )
        existing.block_reasons = block_reasons or None

        self.session.flush()
        return existing

    def delete_by_snapshot_id(self, id_value: UUID) -> None:
        obj = self.get_by_snapshot_id(id_value)
        if obj is not None:
            self.session.delete(obj)
