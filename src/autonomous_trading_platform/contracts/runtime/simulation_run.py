from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class SimulationRun(BaseModel):
    run_id: str
    experiment_id: str | None = None
    strategy_id: str
    dataset_version: str
    universe_version: str
    start_time: UTCDateTime
    end_time: UTCDateTime | None = None
    execution_config: dict[str, Any]
    status: str
    metrics_snapshot_id: str | None = None
