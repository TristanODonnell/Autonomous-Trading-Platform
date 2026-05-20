"""Registry-native strategy parameter schemas.

Schemas are the canonical validation and normalization layer for strategy
configuration.  They intentionally reject unknown fields so persisted configs
remain reproducible and generation metadata cannot drift silently.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class StrategyParameterSchema(BaseModel):
    """Base class for deterministic parameter schema output."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    def canonical_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json"))


def _number(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number, got {type(value).__name__}")
    return float(value)


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value}")
    return value


class MovingAverageCrossoverParameters(StrategyParameterSchema):
    short_window: StrictInt = 10
    long_window: StrictInt = 30

    @field_validator("short_window", "long_window", mode="before")
    @classmethod
    def _windows_are_positive_ints(cls, value: Any, info: Any) -> int:
        return _positive_int(value, info.field_name)

    @model_validator(mode="after")
    def _short_before_long(self) -> MovingAverageCrossoverParameters:
        if self.short_window >= self.long_window:
            raise ValueError(
                f"short_window ({self.short_window}) must be strictly less than "
                f"long_window ({self.long_window})"
            )
        return self


class MomentumParameters(StrategyParameterSchema):
    lookback: StrictInt = 5
    buy_above: float = 0.0
    sell_below: float = 0.0

    @field_validator("lookback", mode="before")
    @classmethod
    def _lookback_is_positive_int(cls, value: Any, info: Any) -> int:
        return _positive_int(value, info.field_name)

    @field_validator("buy_above", "sell_below", mode="before")
    @classmethod
    def _thresholds_are_numbers(cls, value: Any, info: Any) -> float:
        return _number(value, info.field_name)

    @model_validator(mode="after")
    def _sell_not_above_buy(self) -> MomentumParameters:
        if self.sell_below > self.buy_above:
            raise ValueError(
                f"sell_below ({self.sell_below}) must be less than or equal to "
                f"buy_above ({self.buy_above})"
            )
        return self


class MeanReversionParameters(StrategyParameterSchema):
    window: StrictInt = 20
    buy_below_z: float = -2.0
    sell_above_z: float = 2.0

    @field_validator("window", mode="before")
    @classmethod
    def _window_is_positive_int(cls, value: Any, info: Any) -> int:
        return _positive_int(value, info.field_name)

    @field_validator("buy_below_z", "sell_above_z", mode="before")
    @classmethod
    def _thresholds_are_numbers(cls, value: Any, info: Any) -> float:
        return _number(value, info.field_name)

    @model_validator(mode="after")
    def _buy_below_sell(self) -> MeanReversionParameters:
        if self.buy_below_z >= self.sell_above_z:
            raise ValueError(
                f"buy_below_z ({self.buy_below_z}) must be strictly less than "
                f"sell_above_z ({self.sell_above_z})"
            )
        return self


class FactorBasedParameters(StrategyParameterSchema):
    momentum_lookback: StrictInt = 5
    mean_reversion_window: StrictInt = 20
    volatility_window: StrictInt = 20
    volume_window: StrictInt = 20
    buy_score_threshold: float = 0.5
    sell_score_threshold: float = -0.5
    momentum_weight: float = Field(default=0.4, ge=0.0)
    mean_reversion_weight: float = Field(default=0.3, ge=0.0)
    volume_weight: float = Field(default=0.2, ge=0.0)
    volatility_weight: float = Field(default=0.1, ge=0.0)

    @field_validator(
        "momentum_lookback",
        "mean_reversion_window",
        "volatility_window",
        "volume_window",
        mode="before",
    )
    @classmethod
    def _windows_are_positive_ints(cls, value: Any, info: Any) -> int:
        parsed = _positive_int(value, info.field_name)
        if info.field_name == "volatility_window" and parsed <= 1:
            raise ValueError(f"{info.field_name} must be > 1, got {parsed}")
        return parsed

    @field_validator(
        "buy_score_threshold",
        "sell_score_threshold",
        "momentum_weight",
        "mean_reversion_weight",
        "volume_weight",
        "volatility_weight",
        mode="before",
    )
    @classmethod
    def _floats_are_numbers(cls, value: Any, info: Any) -> float:
        return _number(value, info.field_name)

    @model_validator(mode="after")
    def _sell_below_buy(self) -> FactorBasedParameters:
        if self.sell_score_threshold >= self.buy_score_threshold:
            raise ValueError(
                f"sell_score_threshold ({self.sell_score_threshold}) must be strictly less "
                f"than buy_score_threshold ({self.buy_score_threshold})"
            )
        return self


class RandomParameters(StrategyParameterSchema):
    signal_probability: float = Field(default=0.33, ge=0.0, le=1.0)
    buy_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    random_seed: StrictInt | None = None

    @field_validator("signal_probability", "buy_probability", mode="before")
    @classmethod
    def _probabilities_are_numbers(cls, value: Any, info: Any) -> float:
        return _number(value, info.field_name)


class StubParameters(StrategyParameterSchema):
    price_change_threshold: float = Field(default=0.0, ge=0.0)

    @field_validator("price_change_threshold", mode="before")
    @classmethod
    def _threshold_is_number(cls, value: Any, info: Any) -> float:
        return _number(value, info.field_name)


class IntentionalLoserParameters(StubParameters):
    pass


SCHEMAS_BY_STRATEGY: dict[str, type[StrategyParameterSchema]] = {
    "moving_average_crossover": MovingAverageCrossoverParameters,
    "momentum": MomentumParameters,
    "mean_reversion": MeanReversionParameters,
    "factor_based": FactorBasedParameters,
    "random": RandomParameters,
    "stub": StubParameters,
    "intentional_loser": IntentionalLoserParameters,
}


def normalize_with_schema(
    schema: type[StrategyParameterSchema], parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], schema.model_validate(parameters or {}).canonical_dict())
