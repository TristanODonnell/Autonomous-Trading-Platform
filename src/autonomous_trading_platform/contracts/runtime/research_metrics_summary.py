from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.runtime.metric_lineage import (
    MetricLineageMetadata,
    MetricLineageType,
)


class ResearchMetricsSummary(BaseModel):
    """Metrics derived exclusively from backtests, simulations, and experiments.

    Immutable after creation: changing live performance must never mutate these
    values.  Governance promotion workflows use this type to enforce that
    promotion evidence is always traceable to a specific simulation run.
    """

    metrics_snapshot_id: str
    strategy_id: str | None = None
    run_id: str
    created_at: UTCDateTime

    metric_lineage_type: MetricLineageType = MetricLineageType.RESEARCH
    lineage: MetricLineageMetadata

    total_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    trade_count: int | None = None
    winning_trade_count: int | None = None
    losing_trade_count: int | None = None
    volatility: float | None = None

    metrics_json: dict[str, Any] | None = None

    @property
    def win_rate(self) -> float | None:
        if self.trade_count and self.trade_count > 0 and self.winning_trade_count is not None:
            return self.winning_trade_count / self.trade_count
        return None
