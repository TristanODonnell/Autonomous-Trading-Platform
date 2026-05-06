# autonomous_trading_platform/storage/sor/models/run_manifests.py

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Date, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime
from autonomous_trading_platform.governance.models.governance_state import GovernanceState

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, UTCDateTimeType


class RunManifestRow(Base):
    __tablename__ = "run_manifests"

    run_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    run_type: Mapped[RunType] = mapped_column(SAEnum(RunType, name="run_type_enum"), nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(), nullable=False, default=lambda: datetime.now(UTC)
    )
    environment: Mapped[str] = mapped_column(String, nullable=False)
    broker: Mapped[Literal["alpaca"]] = mapped_column(String, nullable=False)
    broker_account_id: Mapped[str] = mapped_column(String, nullable=False)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String, nullable=False)
    strategy_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    capital_bucket: Mapped[Money] = mapped_column(MoneyType(), nullable=False)
    interval: Mapped[BarInterval] = mapped_column(
        SAEnum(BarInterval, name="bar_interval_enum"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    dataset_version: Mapped[str] = mapped_column(String, nullable=False)
    price_basis: Mapped[PriceBasis] = mapped_column(
        SAEnum(PriceBasis, name="price_basis_enum"),
        nullable=False,
    )
    universe_version: Mapped[str] = mapped_column(String, nullable=False)
    cost_model: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    fill_model: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    git_commit: Mapped[str] = mapped_column(String, nullable=False)
    docker_image: Mapped[str | None] = mapped_column(String, nullable=True)
    python_version: Mapped[str | None] = mapped_column(String, nullable=True)
    dependency_lock_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    bar_timestamp: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    last_successful_step: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    artifact_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    schema_definition: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    governance_state: Mapped[GovernanceState] = mapped_column(
        SAEnum(GovernanceState, name="governance_state_enum"),
        nullable=False,
    )
