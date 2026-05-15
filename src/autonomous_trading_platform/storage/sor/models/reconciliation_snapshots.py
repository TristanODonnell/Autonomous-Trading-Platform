from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, UTCDateTimeType, uuid_pk_default


class ReconciliationSnapshot(Base):
    """One row per check per reconciliation run. Append-only — no upsert."""

    __tablename__ = "reconciliation_snapshots"

    snapshot_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid_pk_default)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reconciled_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    check_type: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)

    expected_value: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(24, 9), nullable=True)
    actual_value: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(24, 9), nullable=True)
    delta: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(24, 9), nullable=True)
    tolerance: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(24, 9), nullable=True)

    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(sa.Text(), nullable=False)
