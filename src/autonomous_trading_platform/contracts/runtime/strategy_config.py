from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class StrategyConfig(BaseModel):
    strategy_id: str
    config_hash: str
    config_json: dict[str, Any]
    created_at: UTCDateTime
    strategy_type: str | None = None
    metadata_json: dict[str, Any] | None = None
