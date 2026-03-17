from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class SlippageModelConfig(BaseModel):
    impact_coefficient_bps: Decimal = Field(
        default=Decimal("25"),
        ge=Decimal("0"),
    )
    max_volume_share: Decimal = Field(
        default=Decimal("0.10"),
        gt=Decimal("0"),
        le=Decimal("1"),
    )
