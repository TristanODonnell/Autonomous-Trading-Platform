# autonomous_trading_platform/contracts/common/types.py

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator

# ---- Time Primitive ----


def enforce_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return v.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(enforce_utc)]


# ---- Financial Primitives ----

Money = Decimal
Quantity = Decimal
