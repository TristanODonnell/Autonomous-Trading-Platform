from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.goverance.models.governance_state import GovernanceState


class StrategyGovernanceRecord(BaseModel):
    strategy_id: str
    config_hash: str
    current_state: GovernanceState
    experiment_id: str
    source_run_id: UUID | None  # the simulation run that generated the metrics
    submitted_at: datetime
    updated_at: datetime
    submitted_by: str = "system"  # "system" when auto-proposed from filter output
