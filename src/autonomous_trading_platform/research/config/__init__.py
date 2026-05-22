"""Validated configuration models for the research platform.

All research inputs pass through these Pydantic models before execution begins.
Invalid configs fail fast with clear, field-specific error messages.
"""

from autonomous_trading_platform.research.config.experiment_config import ExperimentConfig
from autonomous_trading_platform.research.config.simulation_run_config import SimulationRunConfig
from autonomous_trading_platform.research.config.stage_configs import (
    FilterConfigModel,
    MonteCarloStageConfigModel,
    ScoringWeightsModel,
    SimulationStageConfigModel,
    WalkForwardStageConfigModel,
)
from autonomous_trading_platform.research.config.strategy_parameter_validators import (
    validate_strategy_parameters,
)

__all__ = [
    "ExperimentConfig",
    "FilterConfigModel",
    "MonteCarloStageConfigModel",
    "ScoringWeightsModel",
    "SimulationRunConfig",
    "SimulationStageConfigModel",
    "WalkForwardStageConfigModel",
    "validate_strategy_parameters",
]
