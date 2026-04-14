# autonomous_trading_platform/contracts/runtime/run_manifest.py
from __future__ import annotations

from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime


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
    universe_version: str
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

    def to_dict(self) -> dict[str, Any]:
        """Return manifest as a Python dictionary."""
        return cast(dict[str, Any], self.model_dump(mode="json"))

    def to_json(self) -> str:
        """Return manifest serialized to JSON."""
        return cast(str, self.model_dump_json(indent=2))
