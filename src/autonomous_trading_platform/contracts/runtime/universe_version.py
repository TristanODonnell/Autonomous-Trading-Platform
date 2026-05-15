# autonomous_trading_platform/contracts/runtime/universe_version.py

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class UniverseVersion(BaseModel):
    universe_version_id: str
    name: str
    source: str
    created_at: UTCDateTime
    effective_from: UTCDateTime
    effective_to: UTCDateTime | None = None
    status: str
    rebalance_reason: str | None = None
    config_hash: str


class UniverseMember(BaseModel):
    universe_version_id: str
    symbol: str
    rank: int | None = None
    score: float | None = None
    included_reason: str | None = None
    excluded_reason: str | None = None
    liquidity_metrics_json: dict[str, Any] | None = None
    quality_metrics_json: dict[str, Any] | None = None
