# autonomous_trading_platform/contracts/validators/core.py

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


class Severity(enum.StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class Violation:
    """
    A single invariant failure.

    - code: stable identifier used in tests / policy routing
    - message: human-readable explanation
    - field: optional field name the violation refers to
    - severity: ERROR blocks ingestion; WARNING can be accepted with logging
    - context: extra structured info for logs/debug (symbol, ts, run_id, etc.)
    """

    code: str
    message: str
    severity: Severity = Severity.ERROR
    field_name: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(v.severity == Severity.ERROR for v in self.violations)

    def add(self, v: Violation) -> None:
        self.violations.append(v)

    def extend(self, vs: Iterable[Violation]) -> None:
        self.violations.extend(vs)
