# autonomous_trading_platform/contracts/runtime/universe_snapshot.py
# DEPRECATED: UniverseSnapshot has been superseded by UniverseVersion + UniverseMember.
# See: contracts/runtime/universe_version.py
# Retained only for the schema drift test backward-compatibility pair.
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class UniverseSnapshot(BaseModel):
    universe_id: str
    snapshot_date: date
    effective_start: UTCDateTime
    effective_end: UTCDateTime | None = None
    symbols: list[str]
    criteria: dict[str, Any]
    version: str
    source: str
    built_at: UTCDateTime
    notes: str | None = None
