from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.runtime.metric_lineage import (
    MetricLineageMetadata,
    MetricLineageType,
)


class LiveMetricsSummary(BaseModel):
    """Metrics derived exclusively from realized fills and runtime execution.

    Used for health monitoring, adaptive allocation, runtime governance, and
    degradation detection.  Never sourced from backtests or simulations.
    """

    snapshot_id: str
    strategy_id: str
    run_id: str | None = None
    computed_at: UTCDateTime

    metric_lineage_type: MetricLineageType = MetricLineageType.LIVE
    lineage: MetricLineageMetadata

    window_days: int | None = None
    window_trades: int | None = None

    realized_return: float | None = None
    rolling_sharpe: float | None = None
    realized_drawdown: float | None = None
    realized_volatility: float | None = None
    live_win_rate: float | None = None
    trade_count: int | None = None
    winning_trade_count: int | None = None
    days_live: int | None = None
    days_since_profitable_day: int | None = None

    metadata_json: dict[str, Any] | None = None
