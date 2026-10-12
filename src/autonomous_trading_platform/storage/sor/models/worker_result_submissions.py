from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class WorkerResultSubmissionRow(Base):
    """One result the worker (Box B) handed to Box A, accepted or rejected (plan 3.1/3.2).

    Keyed by the worker run id: a second submission with the same id returns the first
    decision instead of being re-evaluated, which makes the hand-off idempotent.
    """

    __tablename__ = "worker_result_submissions"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    result_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # accepted / rejected / applied
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worker_host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixture: Mapped[str | None] = mapped_column(String(256), nullable=True)
    produced_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    # The session whose publish index the worker pinned its datasets to.
    dataset_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    dataset_versions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    received_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    applied_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    # Portfolio review that consumed this result, once applied.
    review_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_worker_result_submissions_received_at", "received_at"),
        Index("ix_worker_result_submissions_status", "status"),
    )
