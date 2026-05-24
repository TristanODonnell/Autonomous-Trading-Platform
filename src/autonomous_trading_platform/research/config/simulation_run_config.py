"""Validated Pydantic model for single simulation run configuration.

Use this to validate inputs before constructing SimulationRunRequest.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.simulation.dividend_event import DividendEvent


class SimulationRunConfig(BaseModel):
    """Validated configuration for a single simulation run.

    Validates all fields before SimulationRunRequest is constructed.
    Strategy type is checked against the catalog; dates, capital, and
    seed are range-validated.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    strategy_type: str
    parameters: dict[str, Any] = {}
    dataset_version: str
    random_seed: int
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    initial_cash: float = 100_000.0
    experiment_id: str | None = None
    strict_data_loading: bool = True
    shuffle_timestamp: bool = False
    window_role: str | None = None
    stage_name: str | None = None
    # Settlement delay in simulation trading bars.  0 = immediate (legacy);
    # 1 = T+1 (modern US equities); 2 = T+2 (legacy US equities).
    settlement_days: int = 0
    # Cash dividend events applied during simulation for total-return accuracy.
    # Each event adds (shares_held × cash_amount_per_share) to settled cash on
    # the first bar whose calendar date matches the event's ex_date.
    # Prices are assumed to be split-adjusted when price_basis=ADJUSTED.
    dividend_events: list[DividendEvent] = []

    @field_validator("strategy_id")
    @classmethod
    def _strategy_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("strategy_id must not be empty")
        return v

    @field_validator("strategy_type")
    @classmethod
    def _strategy_type_cataloged(cls, v: str) -> str:
        from autonomous_trading_platform.strategy.catalog import (
            list_strategy_types,
            strategy_type_exists,
        )

        if not strategy_type_exists(v):
            raise ValueError(f"Unknown strategy type {v!r}. Known types: {list_strategy_types()}")
        return v

    @field_validator("dataset_version")
    @classmethod
    def _dataset_version_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("dataset_version must not be empty")
        return v

    @field_validator("random_seed")
    @classmethod
    def _seed_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("random_seed must be >= 0")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols_non_empty(cls, v: list[str]) -> list[str]:
        normalized = [s.strip().upper() for s in v if s.strip()]
        if not normalized:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return normalized

    @field_validator("initial_cash")
    @classmethod
    def _cash_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("initial_cash must be > 0")
        return v

    @field_validator("settlement_days")
    @classmethod
    def _settlement_days_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("settlement_days must be >= 0")
        return v

    @field_validator("dividend_events")
    @classmethod
    def _dividend_events_valid(cls, v: list[DividendEvent]) -> list[DividendEvent]:
        return v

    @model_validator(mode="after")
    def _validate_dates(self) -> SimulationRunConfig:
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be strictly before end_date ({self.end_date})"
            )
        return self
