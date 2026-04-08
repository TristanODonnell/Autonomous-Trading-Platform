from typing import Literal

from pydantic import BaseModel


class Incident(BaseModel):
    # Core classification
    incident_type: str
    domain: Literal["ingestion", "scheduler"]
    severity: Literal["info", "warning", "error", "critical"]

    # Context
    component: str
    job: str | None = None

    # Identifiers
    run_id: str | None = None
    strategy_id: str | None = None

    # Market context
    symbol: str | None = None
    bar_timestamp: str | None = None

    # Versioning
    dataset_version: str | None = None
    universe_version: str | None = None

    # Freeform
    message: str | None = None
