from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class Checksums(Base):
    __tablename__ = "checksums"
    checksum_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_path: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    checksum_value: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
