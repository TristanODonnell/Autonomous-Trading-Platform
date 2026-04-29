from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.common.types import UTCDateTime


class SimulationRun(BaseModel):
    run_id: str
    experiment_id: str | None = None
    strategy_id: str
    dataset_version: str
    universe_version: str
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    window_role: str | None = None  # train | test | fold_N | None
    start_time: UTCDateTime
    end_time: UTCDateTime | None = None
    execution_config: dict[str, Any]
    status: str
    metrics_snapshot_id: str | None = None
