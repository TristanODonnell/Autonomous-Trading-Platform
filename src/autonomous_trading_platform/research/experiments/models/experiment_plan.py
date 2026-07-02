from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.universe.types import UniverseResolutionMode


class ExperimentType(enum.StrEnum):
    AB = "ab"
    SWEEP = "sweep"
    TIME_SEGMENTATION = "time_segmentation"
    ROLLING_WINDOW = "rolling_window"
    CROSS_UNIVERSE = "cross_universe"


@dataclass(slots=True)
class ExperimentDefinition:
    experiment_id: str
    experiment_type: ExperimentType
    description: str | None
    strategy_set: list[dict[str, Any]]
    parameter_grid: list[dict[str, Any]]
    dataset_version: str
    universe_version: str
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    random_seed: int
    initial_cash: float = 100_000.0
    parameter_space: dict[str, list] | None = None
    # TIME_SEGMENTATION
    train_ratio: float | None = None  # e.g. 0.7
    # ROLLING_WINDOW
    window_size_days: int | None = None
    step_size_days: int | None = None
    # CROSS_UNIVERSE
    universe_set: list[list[str]] | None = None
    staged_pipeline_config: StagedPipelineConfig | None = None
    universe_resolution_mode: UniverseResolutionMode | None = None
    resample_to_daily: bool = False
