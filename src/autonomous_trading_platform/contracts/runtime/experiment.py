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

    strategy_set_json: dict[str, Any] | None = None
    parameter_grid_json: dict[str, Any] | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    start_time: UTCDateTime | None = None
    end_time: UTCDateTime | None = None

    metadata_json: dict[str, Any] | None = None
