"""Validated Pydantic model for experiment configuration.

Replace ad hoc YAML parsing and dataclass construction with this model.
All fields are validated before ExperimentDefinition is constructed.
date.today() defaults are explicitly rejected — dates must be supplied.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.universe.types import UniverseResolutionMode


class ExperimentConfig(BaseModel):
    """Validated research experiment configuration.

    Construct this before building ExperimentDefinition. Validation fails
    immediately with field-specific messages rather than propagating bad
    state into the simulation pipeline.
    """

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    experiment_type: ExperimentType = ExperimentType.SWEEP
    description: str | None = None
    strategy_set: list[dict[str, Any]] = Field(default_factory=list)
    parameter_grid: list[dict[str, Any]] = Field(default_factory=list)
    parameter_space: dict[str, list] | None = None
    dataset_version: str
    universe_version: str = "v1"
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    random_seed: int
    initial_cash: float = 100_000.0
    # TIME_SEGMENTATION
    train_ratio: float | None = None
    # ROLLING_WINDOW
    window_size_days: int | None = None
    step_size_days: int | None = None
    # CROSS_UNIVERSE
    universe_set: list[list[str]] | None = None
    universe_resolution_mode: UniverseResolutionMode | None = None

    @field_validator("experiment_id")
    @classmethod
    def _experiment_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("experiment_id must not be empty")
        return v

    @field_validator("dataset_version")
    @classmethod
    def _dataset_version_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("dataset_version must not be empty")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols_non_empty(cls, v: list[str]) -> list[str]:
        normalized = [s.strip().upper() for s in v if s.strip()]
        if not normalized:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return normalized

    @field_validator("random_seed")
    @classmethod
    def _seed_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("random_seed must be >= 0")
        return v

    @field_validator("initial_cash")
    @classmethod
    def _cash_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("initial_cash must be > 0")
        return v

    @field_validator("train_ratio")
    @classmethod
    def _train_ratio_valid(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 < v < 1.0):
            raise ValueError(f"train_ratio must be in (0, 1), got {v}")
        return v

    @field_validator("window_size_days")
    @classmethod
    def _window_size_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("window_size_days must be > 0")
        return v

    @field_validator("step_size_days")
    @classmethod
    def _step_size_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("step_size_days must be > 0")
        return v

    @model_validator(mode="after")
    def _validate_dates(self) -> ExperimentConfig:
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be strictly before end_date ({self.end_date})"
            )
        return self

    @model_validator(mode="after")
    def _validate_type_specific_fields(self) -> ExperimentConfig:
        t = self.experiment_type
        if t == ExperimentType.TIME_SEGMENTATION and self.train_ratio is None:
            raise ValueError("train_ratio is required for TIME_SEGMENTATION experiments")
        if t == ExperimentType.ROLLING_WINDOW:
            if self.window_size_days is None:
                raise ValueError("window_size_days is required for ROLLING_WINDOW experiments")
            if self.step_size_days is None:
                raise ValueError("step_size_days is required for ROLLING_WINDOW experiments")
        if t == ExperimentType.CROSS_UNIVERSE and not self.universe_set:
            raise ValueError(
                "universe_set is required and must be non-empty for CROSS_UNIVERSE experiments"
            )
        return self

    @model_validator(mode="after")
    def _validate_parameter_space(self) -> ExperimentConfig:
        if self.parameter_space is None:
            return self
        for key, value in self.parameter_space.items():
            if not isinstance(value, list):
                raise ValueError(
                    f"parameter_space[{key!r}] must be a list of values, got {type(value).__name__}"
                )
            if not value:
                raise ValueError(f"parameter_space[{key!r}] must not be empty")
        return self

    def to_experiment_definition(self, staged_pipeline_config=None) -> ExperimentDefinition:
        """Construct ExperimentDefinition from this validated config."""
        return ExperimentDefinition(
            experiment_id=self.experiment_id,
            experiment_type=self.experiment_type,
            description=self.description,
            strategy_set=list(self.strategy_set),
            parameter_grid=list(self.parameter_grid),
            parameter_space=self.parameter_space,
            dataset_version=self.dataset_version,
            universe_version=self.universe_version,
            price_basis=self.price_basis,
            symbols=list(self.symbols),
            start_date=self.start_date,
            end_date=self.end_date,
            random_seed=self.random_seed,
            initial_cash=self.initial_cash,
            train_ratio=self.train_ratio,
            window_size_days=self.window_size_days,
            step_size_days=self.step_size_days,
            universe_set=self.universe_set,
            universe_resolution_mode=self.universe_resolution_mode,
            staged_pipeline_config=staged_pipeline_config,
        )
