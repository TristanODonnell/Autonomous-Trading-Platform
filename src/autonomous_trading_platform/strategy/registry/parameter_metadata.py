"""Parameter specification and search-space metadata for strategy definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ParameterType(StrEnum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STRING = "string"


@dataclass(frozen=True)
class ParameterSpec:
    """Declarative description of one strategy parameter and its search space."""

    name: str
    parameter_type: ParameterType
    default: Any
    description: str
    min_value: float | None = None
    max_value: float | None = None
    discrete: bool = False
    step: float | None = None
    tunable: bool = True
