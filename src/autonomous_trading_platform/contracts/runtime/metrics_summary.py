from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.runtime.metric_lineage import MetricLineageType


class MetricsSummary(BaseModel):
    metrics_snapshot_id: str
    run_id: str
    created_at: UTCDateTime

    total_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    trade_count: int | None = None
    winning_trade_count: int | None = None
    losing_trade_count: int | None = None
    volatility: float | None = None

    metrics_json: dict[str, Any] | None = None

    # Lineage fields — optional for backward compatibility; populated for all new writes.
    metric_lineage_type: MetricLineageType | None = MetricLineageType.RESEARCH
    environment: str | None = None
    calculation_version: str | None = None
    lookback_window_days: int | None = None
    source_strategy_ids: list[str] | None = None
