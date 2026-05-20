"""Validated Pydantic models for pipeline stage configuration.

Each stage type (simulation, walk_forward, monte_carlo) has a corresponding
Pydantic model that validates the raw YAML dict before the stage dataclass
is constructed. Validation failures are raised before execution begins.

Usage in stage from_dict()
--------------------------
    validated = SimulationStageConfigModel.model_validate(raw)
    stage_cfg = SimulationStageConfig(
        name=validated.name,
        start_date=validated.start_date,
        ...
        filter_config=validated.filter_config.to_dataclass(),
        scoring_weights=validated.scoring_weights.to_dataclass(),
    )
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)

# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------


class FilterConfigModel(BaseModel):
    """Validated input for FilterConfig.

    Rejects semantically invalid thresholds: max_drawdown must be <= 0,
    rate-based fields must be in [0, 1], trade counts must be non-negative.
    """

    model_config = ConfigDict(frozen=True)

    min_sharpe: float = 1.0
    max_drawdown: float = -0.20
    min_trades: int = 30
    min_consistency_score: float = 0.5
    min_profit_factor: float = 1.0
    min_win_rate: float = 0.0
    max_return_variance: float | None = None
    min_total_return: float = 0.0
    robustness_min_sharpe: float = 0.0
    robustness_min_profitable_windows: int = 0

    @field_validator("max_drawdown")
    @classmethod
    def _drawdown_non_positive(cls, v: float) -> float:
        if v > 0:
            raise ValueError(
                f"max_drawdown must be <= 0 (a drawdown is a loss), got {v}. "
                "Use a negative value such as -0.20 for a 20% maximum drawdown."
            )
        return v

    @field_validator("min_consistency_score")
    @classmethod
    def _consistency_score_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"min_consistency_score must be in [0, 1], got {v}")
        return v

    @field_validator("min_win_rate")
    @classmethod
    def _win_rate_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"min_win_rate must be in [0, 1], got {v}")
        return v

    @field_validator("min_profit_factor")
    @classmethod
    def _profit_factor_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"min_profit_factor must be >= 0, got {v}")
        return v

    @field_validator("min_trades")
    @classmethod
    def _min_trades_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"min_trades must be >= 0, got {v}")
        return v

    @field_validator("max_return_variance")
    @classmethod
    def _variance_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"max_return_variance must be > 0 if set, got {v}")
        return v

    @field_validator("robustness_min_profitable_windows")
    @classmethod
    def _robustness_windows_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"robustness_min_profitable_windows must be >= 0, got {v}")
        return v

    def to_dataclass(self) -> FilterConfig:
        return FilterConfig(
            min_sharpe=self.min_sharpe,
            max_drawdown=self.max_drawdown,
            min_trades=self.min_trades,
            min_consistency_score=self.min_consistency_score,
            min_profit_factor=self.min_profit_factor,
            min_win_rate=self.min_win_rate,
            max_return_variance=self.max_return_variance,
            min_total_return=self.min_total_return,
            robustness_min_sharpe=self.robustness_min_sharpe,
            robustness_min_profitable_windows=self.robustness_min_profitable_windows,
        )


class ScoringWeightsModel(BaseModel):
    """Validated input for ScoringWeights.

    All weights must be >= 0.
    """

    model_config = ConfigDict(frozen=True)

    w_sharpe: float = 0.4
    w_return: float = 0.3
    w_drawdown: float = 0.2
    w_consistency: float = 0.1

    @field_validator("w_sharpe", "w_return", "w_drawdown", "w_consistency")
    @classmethod
    def _weight_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"Scoring weight must be >= 0, got {v}")
        return v

    def to_dataclass(self) -> ScoringWeights:
        return ScoringWeights(
            w_sharpe=self.w_sharpe,
            w_return=self.w_return,
            w_drawdown=self.w_drawdown,
            w_consistency=self.w_consistency,
        )


# ---------------------------------------------------------------------------
# SimulationStage
# ---------------------------------------------------------------------------


class SimulationStageConfigModel(BaseModel):
    """Validated YAML input for SimulationStage."""

    model_config = ConfigDict(frozen=True)

    name: str
    start_date: date
    end_date: date
    symbols: list[str]
    filter_config: FilterConfigModel = FilterConfigModel()
    scoring_weights: ScoringWeightsModel = ScoringWeightsModel()
    window_role: str | None = None

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("stage name must not be empty")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols_non_empty(cls, v: list[str]) -> list[str]:
        normalized = [s.strip().upper() for s in v if s.strip()]
        if not normalized:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return normalized

    @model_validator(mode="after")
    def _validate_dates(self) -> SimulationStageConfigModel:
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be strictly before end_date ({self.end_date})"
            )
        return self


# ---------------------------------------------------------------------------
# WalkForwardStage
# ---------------------------------------------------------------------------


class WalkForwardStageConfigModel(BaseModel):
    """Validated YAML input for WalkForwardStage."""

    model_config = ConfigDict(frozen=True)

    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    train_days: int
    test_days: int
    step_days: int
    train_filter_config: FilterConfigModel = FilterConfigModel()
    train_scoring_weights: ScoringWeightsModel = ScoringWeightsModel()
    test_filter_config: FilterConfigModel = FilterConfigModel()
    test_scoring_weights: ScoringWeightsModel = ScoringWeightsModel()
    require_all_folds: bool = True
    min_folds_passed: int = 1

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("stage name must not be empty")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols_non_empty(cls, v: list[str]) -> list[str]:
        normalized = [s.strip().upper() for s in v if s.strip()]
        if not normalized:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return normalized

    @field_validator("train_days", "test_days", "step_days")
    @classmethod
    def _days_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"train_days, test_days, and step_days must each be > 0, got {v}")
        return v

    @field_validator("min_folds_passed")
    @classmethod
    def _min_folds_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"min_folds_passed must be >= 1, got {v}")
        return v

    @model_validator(mode="after")
    def _validate_dates_and_window(self) -> WalkForwardStageConfigModel:
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be strictly before end_date ({self.end_date})"
            )
        total_days = (self.end_date - self.start_date).days
        min_span = self.train_days + self.test_days
        if total_days < min_span:
            raise ValueError(
                f"Date range ({total_days} days) is shorter than "
                f"train_days + test_days ({min_span} days). No folds can be generated."
            )
        return self


# ---------------------------------------------------------------------------
# MonteCarloStage
# ---------------------------------------------------------------------------


class MonteCarloStageConfigModel(BaseModel):
    """Validated YAML input for MonteCarloStage."""

    model_config = ConfigDict(frozen=True)

    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    n_runs: int = 30
    min_pass_rate: float = 0.70
    filter_config: FilterConfigModel = FilterConfigModel()
    scoring_weights: ScoringWeightsModel = ScoringWeightsModel()

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("stage name must not be empty")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols_non_empty(cls, v: list[str]) -> list[str]:
        normalized = [s.strip().upper() for s in v if s.strip()]
        if not normalized:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return normalized

    @field_validator("n_runs")
    @classmethod
    def _n_runs_at_least_two(cls, v: int) -> int:
        if v < 2:
            raise ValueError(f"n_runs must be >= 2 for Monte Carlo to be meaningful, got {v}")
        return v

    @field_validator("min_pass_rate")
    @classmethod
    def _pass_rate_valid(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError(f"min_pass_rate must be in (0, 1], got {v}")
        return v

    @model_validator(mode="after")
    def _validate_dates(self) -> MonteCarloStageConfigModel:
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be strictly before end_date ({self.end_date})"
            )
        return self
