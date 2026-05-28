from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class MetricLineageType(StrEnum):
    RESEARCH = "research"
    LIVE = "live"
    BLENDED = "blended"


class MetricLineageMetadata(BaseModel):
    """Provenance record attached to every metric summary.

    Captures enough context to answer: where did this number come from,
    when was it computed, over what window, and under what environment?
    Stored alongside metric values so dashboards and governance workflows
    can display or filter by lineage without joining auxiliary tables.
    """

    metric_lineage_type: MetricLineageType
    environment: str | None = None
    calculation_version: str = "1.0"
    generated_at: UTCDateTime
    lookback_window_days: int | None = None
    source_run_ids: list[str] = []
    source_strategy_ids: list[str] = []
    extra: dict[str, Any] | None = None
