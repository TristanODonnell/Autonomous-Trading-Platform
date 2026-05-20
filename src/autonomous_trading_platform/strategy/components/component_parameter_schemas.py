"""Shared component parameter metadata for strategy components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ComponentParameterType(StrEnum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STRING = "string"


@dataclass(frozen=True)
class ComponentParameterSpec:
    """Declarative description of one component parameter and search-space hints."""

    name: str
    parameter_type: ComponentParameterType
    default: Any
    description: str
    min_value: float | None = None
    max_value: float | None = None
    discrete: bool = False
    step: float | None = None
    tunable: bool = True
    mutation_strategy: str | None = None


def int_parameter(
    name: str,
    *,
    default: int,
    description: str,
    min_value: int,
    max_value: int,
) -> ComponentParameterSpec:
    return ComponentParameterSpec(
        name=name,
        parameter_type=ComponentParameterType.INT,
        default=default,
        description=description,
        min_value=min_value,
        max_value=max_value,
        discrete=True,
        step=1,
        tunable=True,
        mutation_strategy="step",
    )


def float_parameter(
    name: str,
    *,
    default: float,
    description: str,
    min_value: float,
    max_value: float,
    tunable: bool = True,
) -> ComponentParameterSpec:
    return ComponentParameterSpec(
        name=name,
        parameter_type=ComponentParameterType.FLOAT,
        default=default,
        description=description,
        min_value=min_value,
        max_value=max_value,
        discrete=False,
        tunable=tunable,
        mutation_strategy="gaussian" if tunable else "fixed",
    )
