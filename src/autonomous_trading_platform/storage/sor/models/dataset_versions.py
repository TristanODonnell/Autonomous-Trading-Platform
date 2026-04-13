from __future__ import annotations

from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
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

    price_basis: Mapped[PriceBasis | None] = mapped_column(
        SAEnum(PriceBasis, name="price_basis_enum"),
        nullable=True,
    )
    interval: Mapped[BarInterval | None] = mapped_column(
        SAEnum(BarInterval, name="bar_interval_enum"),
        nullable=True,
    )
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
