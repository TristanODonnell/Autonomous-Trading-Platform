from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class DatasetVersions(Base):
    __tablename__ = "dataset_versions"

    dataset_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    price_basis: Mapped[PriceBasis] = mapped_column(
        SAEnum(PriceBasis, name="price_basis_enum"),
        nullable=False,
    )
    interval: Mapped[BarInterval] = mapped_column(
        SAEnum(BarInterval, name="bar_interval_enum"),
        nullable=False,
    )

    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_coverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_coverage_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_coverage_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_dataset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
