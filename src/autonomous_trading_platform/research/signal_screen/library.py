"""The rows the screen tests, written down before a run.

A **row** is one idea a strategy could trade on, and which way it says the price goes:

    Row(id="z20_low_2", scale="5min", direction=1,
        conditions=(Condition(reading("z_score", window=20), "<", -2.0),), ...)

reads "after a bar where the 20-bar z-score is under -2, the return is higher than on
other days". Every row is tested at every horizon of its scale.

- A **reading** is one of the 13 indicators with its settings. ``offset=-1`` is its
  value one decision earlier, as a composite's crossover rule reads it: the same bars
  less the last one.
- A **condition** compares a reading with a number, or with another reading. A row
  holds where every one of its conditions holds.
- The **direction** is declared here, so the test is one-sided: a row that turns out
  strongly wrong the other way is not a finding. The opposite idea is its own row.

**Units.** A z-score of -2 means the same for every name; a moving average of 150
dollars does not. A reading in dollars or shares may be compared with 0 (its sign) or
with another reading in the same unit, and with nothing else.

**The hash.** ``Library.content_hash`` covers what is tested (ids, scales, conditions,
directions) and not the prose. A report carries it, and a test pins the hash of the
library a run is declared with, so a row changed after results were seen shows.
"""

from __future__ import annotations

import hashlib
import json
import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from .indicators import INDICATORS, IndicatorInput, indicator_column
from .panel import Scale
from .statistics import Direction

Operator = Literal[">", "<", ">=", "<="]

_OPERATORS: dict[str, Callable[[Any, Any], Any]] = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
}

# What each indicator's number is measured in.
UNITS: dict[str, str] = {
    "simple_moving_average": "dollars",
    "exponential_moving_average": "dollars",
    "distance_from_moving_average": "dollars",
    "momentum": "dollars",
    "rolling_standard_deviation": "dollars",
    "average_volume": "shares",
    "z_score": "none",
    "rate_of_change": "none",
    "rsi": "none",
    "rsi_wilder": "none",
    "realized_volatility": "none",
    "volume_ratio": "none",
    "volume_spike": "none",
}

# Settings that say how many bars back a reading reaches.
_REACH = ("window", "lookback", "context_bars")


@dataclass(frozen=True)
class Reading:
    indicator: str
    parameters: tuple[tuple[str, float], ...] = ()
    # 0: the decision's own value. -1: the value one decision earlier, and so on.
    offset: int = 0

    def __post_init__(self) -> None:
        if self.indicator not in INDICATORS:
            raise ValueError(
                f"unknown indicator {self.indicator!r}; the screen knows {sorted(INDICATORS)}"
            )
        if list(self.parameters) != sorted(self.parameters):
            raise ValueError("parameters are given in name order (use reading())")
        if self.offset > 0:
            raise ValueError("an offset looks back: 0 or negative")

    @property
    def unit(self) -> str:
        return UNITS[self.indicator]

    @property
    def bars(self) -> int:
        """How many bars back it reaches."""
        reach = max([int(value) for key, value in self.parameters if key in _REACH], default=1)
        return reach - self.offset

    @property
    def label(self) -> str:
        settings = ", ".join(f"{key}={value:g}" for key, value in self.parameters)
        earlier = f"[{self.offset}]" if self.offset else ""
        return f"{self.indicator}({settings}){earlier}"

    def column(self, data: IndicatorInput) -> np.ndarray:
        column = indicator_column(self.indicator, data, **dict(self.parameters))
        if not self.offset:
            return column
        back = min(-self.offset, len(column))
        return np.concatenate([np.full(back, np.nan), column[: len(column) - back]])


def reading(indicator: str, *, offset: int = 0, **parameters: float) -> Reading:
    return Reading(indicator=indicator, parameters=tuple(sorted(parameters.items())), offset=offset)


@dataclass(frozen=True)
class Condition:
    left: Reading
    operator: Operator
    right: Reading | float

    def __post_init__(self) -> None:
        if self.operator not in _OPERATORS:
            raise ValueError(f"an operator is one of {sorted(_OPERATORS)}, got {self.operator!r}")
        if isinstance(self.right, Reading):
            if self.left.unit != self.right.unit:
                raise ValueError(
                    f"{self.left.label} is in {self.left.unit} and {self.right.label} in "
                    f"{self.right.unit}: they cannot be compared"
                )
        elif self.left.unit != "none" and float(self.right) != 0.0:
            raise ValueError(
                f"{self.left.label} is in {self.left.unit}, which differ from name to name: "
                "compare it with 0 or with another reading"
            )

    @property
    def readings(self) -> tuple[Reading, ...]:
        if isinstance(self.right, Reading):
            return (self.left, self.right)
        return (self.left,)

    @property
    def label(self) -> str:
        right = self.right.label if isinstance(self.right, Reading) else f"{self.right:g}"
        return f"{self.left.label} {self.operator} {right}"

    def column(self, data: IndicatorInput) -> np.ndarray:
        """1.0 where it holds, 0.0 where it does not, NaN where a reading has no value."""
        left = self.left.column(data)
        if isinstance(self.right, Reading):
            right: np.ndarray | float = self.right.column(data)
            missing = np.isnan(left) | np.isnan(right)
        else:
            right = float(self.right)
            missing = np.isnan(left)
        with np.errstate(invalid="ignore"):
            held = _OPERATORS[self.operator](left, right)
        return np.where(missing, np.nan, held.astype(float))


@dataclass(frozen=True)
class Row:
    id: str
    # A heading the report groups rows under.
    family: str
    scale: Scale
    conditions: tuple[Condition, ...]
    # 1: the return after is predicted higher than on other days. -1: lower.
    direction: Direction
    # The idea in a sentence.
    reads: str

    def __post_init__(self) -> None:
        if not self.id or not self.conditions:
            raise ValueError("a row has an id and at least one condition")
        if self.scale not in ("5min", "daily"):
            raise ValueError(f"a scale is '5min' or 'daily', got {self.scale!r}")
        if self.direction not in (1, -1):
            raise ValueError("direction is 1 (higher) or -1 (lower)")

    @property
    def readings(self) -> tuple[Reading, ...]:
        return tuple(r for condition in self.conditions for r in condition.readings)

    @property
    def bars(self) -> int:
        """How many bars back its slowest reading reaches."""
        return max(r.bars for r in self.readings)

    @property
    def label(self) -> str:
        return " and ".join(condition.label for condition in self.conditions)

    @property
    def predicts(self) -> str:
        return "higher" if self.direction == 1 else "lower"

    def column(self, data: IndicatorInput) -> np.ndarray:
        """1.0 where every condition holds, 0.0 where one does not, NaN where a
        reading has no value."""
        columns = [condition.column(data) for condition in self.conditions]
        missing = np.any([np.isnan(column) for column in columns], axis=0)
        held = np.all([np.nan_to_num(column) > 0 for column in columns], axis=0)
        return np.where(missing, np.nan, held.astype(float))

    def tested(self) -> dict[str, Any]:
        """What the hash covers."""
        return {
            "id": self.id,
            "scale": self.scale,
            "direction": self.direction,
            "conditions": [
                {
                    "left": _reading_json(condition.left),
                    "operator": condition.operator,
                    "right": (
                        _reading_json(condition.right)
                        if isinstance(condition.right, Reading)
                        else float(condition.right)
                    ),
                }
                for condition in self.conditions
            ],
        }

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "scale": self.scale,
            "condition": self.label,
            "predicts": self.predicts,
            "reads": self.reads,
        }


def _reading_json(value: Reading) -> dict[str, Any]:
    return {
        "indicator": value.indicator,
        "parameters": {key: float(setting) for key, setting in value.parameters},
        "offset": value.offset,
    }


@dataclass(frozen=True)
class Library:
    name: str
    rows: tuple[Row, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("a library has at least one row")
        ids = [row.id for row in self.rows]
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ValueError(f"row ids are used once each; repeated: {repeated}")

    def content_hash(self) -> str:
        body = json.dumps(
            {"name": self.name, "rows": [row.tested() for row in self.rows]},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def on_scale(self, scale: Scale) -> tuple[Row, ...]:
        return tuple(row for row in self.rows if row.scale == scale)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hash": self.content_hash(),
            "rows": [row.describe() for row in self.rows],
        }
