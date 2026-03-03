# autonomous_trading_platform/contracts/runtime/universe_snapshot.py
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class UniverseSnapshot(BaseModel):
    universe_id: str
    snapshot_date: date
    effective_start: datetime
    effective_end: datetime | None = None
    symbols: list[str]
    criteria: dict[str, Any]
    version: str
    source: str
    built_at: datetime
    notes: str | None = None
