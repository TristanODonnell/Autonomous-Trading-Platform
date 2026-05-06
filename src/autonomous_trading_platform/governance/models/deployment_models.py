# autonomous_trading_platform/governance/models/deployment_models.py

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from autonomous_trading_platform.governance.models.governance_state import GovernanceState


class DeploymentEnvironment(enum.StrEnum):
    PAPER = "paper"
    LIVE = "live"


class DeploymentStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class DeploymentRecord(BaseModel):
    """Immutable snapshot of a single strategy deployment."""

    deployment_id: str
    strategy_id: str
    environment: DeploymentEnvironment
    status: DeploymentStatus
    governance_state_at_deploy: GovernanceState
    deployed_by: str
    deployed_at: datetime
    updated_at: datetime
    # Optional rollback pointer — set when this record supersedes a previous one
    rolled_back_from_deployment_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
