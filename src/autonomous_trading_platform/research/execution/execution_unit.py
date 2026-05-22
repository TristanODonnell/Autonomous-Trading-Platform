from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ExecutionUnit(Generic[T]):
    unit_id: str
    sort_key: tuple[Any, ...]
    run: Callable[[], T]
    metadata: dict[str, Any] = field(default_factory=dict)
