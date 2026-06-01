from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, UTCDateTimeType, uuid_pk_default


class RuntimeJobRunSteps(Base):
    __tablename__ = "runtime_job_run_steps"

    step_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid_pk_default)

    job_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("runtime_job_runs.job_run_id"),
        nullable=False,
    )

    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    started_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    completed_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)

    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
