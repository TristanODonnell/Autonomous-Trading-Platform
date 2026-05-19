from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class RawMarketSymbol(BaseModel):
    symbol: str
    exchange: str | None
    asset_type: str
    status: str
    is_tradable: bool
    name: str | None = None
    currency: str | None = None
    country: str | None = None
    marginable: bool | None = None
    shortable: bool | None = None
    fractionable: bool | None = None
    easy_to_borrow: bool | None = None
    provider_symbol: str | None = None
    provider_asset_id: str | None = None
    source: str
    first_seen: UTCDateTime
    last_seen: UTCDateTime
    metadata_json: dict[str, Any] | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class RawMarketPoolSnapshot(BaseModel):
    snapshot_id: str
    captured_at: UTCDateTime
    source: str
    symbol_count: int
    cadence: str
    is_complete: bool
    metadata_json: dict[str, Any] | None = None


class RawMarketPoolMembership(BaseModel):
    snapshot_id: str
    symbol: str
    is_tradable: bool
    status: str
    asset_type: str
    exchange: str | None = None
