from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class Experiment(BaseModel):
    experiment_id: str
    experiment_name: str
    created_at: UTCDateTime
    description: str | None = None
    status: str
    metadata_json: dict[str, Any] | None = None
