from __future__ import annotations

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.runtime.live_metrics_summary import LiveMetricsSummary
from autonomous_trading_platform.contracts.runtime.metric_lineage import (
    MetricLineageMetadata,
    MetricLineageType,
)
from autonomous_trading_platform.contracts.runtime.research_metrics_summary import (
    ResearchMetricsSummary,
)


class BlendedMetricsSummary(BaseModel):
    """Confidence-weighted combination of research and live metrics.

    alpha = live weight (increases as strategy accumulates runtime history).
    blended_score = alpha * live_score + (1 - alpha) * research_score.

    Used for adaptive allocation and strategy quality scoring.  Never used as
    governance evidence — promotion/demotion workflows must reference
    ResearchMetricsSummary or LiveMetricsSummary directly.
    """

    blended_snapshot_id: str
    strategy_id: str
    computed_at: UTCDateTime
    rebalance_run_id: str | None = None

    metric_lineage_type: MetricLineageType = MetricLineageType.BLENDED
    lineage: MetricLineageMetadata

    research_metrics: ResearchMetricsSummary | None = None
    live_metrics: LiveMetricsSummary | None = None

    alpha: float
    days_live: int | None = None
    trade_count: int | None = None

    research_score: float
    live_score: float
    blended_score: float

    research_metrics_snapshot_id: str | None = None
    live_metrics_snapshot_id: str | None = None
