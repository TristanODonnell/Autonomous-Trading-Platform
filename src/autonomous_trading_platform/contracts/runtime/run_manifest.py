# autonomous_trading_platform/contracts/runtime/run_manifest.py
from __future__ import annotations

from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime
from autonomous_trading_platform.governance.models.governance_state import GovernanceState


class RunManifest(BaseModel):
    run_id: UUID
    run_type: RunType
    created_at: UTCDateTime
    environment: str
    broker: Literal["alpaca"]
    broker_account_id: str
    strategy_id: str
    strategy_version: str
    strategy_config: dict[str, Any]
    capital_bucket: Money
    interval: BarInterval
    start_date: date
    end_date: date | None = None
    dataset_version: str
    price_basis: PriceBasis
    universe_version: str
    universe_version_id: str | None = None
    universe_source: str | None = None
    universe_member_count: int | None = None
    universe_resolution_mode: str | None = None
    universe_effective_timestamp: UTCDateTime | None = None
    replay_rotation_enabled: bool = False
    cost_model: dict[str, Any] | None = None
    fill_model: dict[str, Any] | None = None
    random_seed: int | None = None
    git_commit: str
    docker_image: str | None = None
    python_version: str | None = None
    dependency_lock_hash: str | None = None
    notes: str | None = None
    bar_timestamp: UTCDateTime | None = None
    status: str | None = None
    current_step: str | None = None
    last_successful_step: str | None = None
    error_message: str | None = None
    artifact_manifest: dict[str, Any] | None = None
    schema_definition: dict[str, Any] | None = None
    governance_state: GovernanceState
    # Settlement delay applied during simulation (A-03 / F-06).
    settlement_days: int | None = None
    # Corporate-action lineage recorded for reproducibility (A-02).
    # dividend_events_count: number of dividend events applied.
    # dividend_events_hash: 16-char SHA-256 of sorted events; empty = none applied.
    # price_adjustment_basis: "split_adjusted" | "raw" reflecting the dataset assumption.
    dividend_events_count: int | None = None
    dividend_events_hash: str | None = None
    price_adjustment_basis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return manifest as a Python dictionary."""
        return cast(dict[str, Any], self.model_dump(mode="json"))

    def to_json(self) -> str:
        """Return manifest serialized to JSON."""
        return cast(str, self.model_dump_json(indent=2))
