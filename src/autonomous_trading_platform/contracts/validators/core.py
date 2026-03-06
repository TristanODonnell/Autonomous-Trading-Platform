# autonomous_trading_platform/contracts/validators/core.py

from __future__ import annotations

import enum
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

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
    field: str | None = None
    context: Mapping[str, Any] = dc_field(default_factory=dict)


@dataclass
class ValidationResult:
    violations: list[Violation] = dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(v.severity == Severity.ERROR for v in self.violations)

    def add(self, v: Violation) -> None:
        self.violations.append(v)

    def extend(self, vs: Iterable[Violation]) -> None:
        self.violations.extend(vs)


@dataclass(frozen=True)
class ValidationContext:
    """
    Optional shared context passed to rules.
    Add fields only when you actually need them.

    Examples:
      - run_id for better context in violations
      - prev record for transition rules
      - lookup tables (intent_by_id, order_by_id) for cross-object checks
    """

    run_id: str | None = None
    prev: Any | None = None
    lookups: Mapping[str, Any] = dc_field(default_factory=dict)


def _default_message(_obj: Any, _ctx: ValidationContext) -> str:
    return "Invariant violated"


def _default_context(_obj: Any, _ctx: ValidationContext) -> Mapping[str, Any]:
    return {}


@dataclass(frozen=True)
class Rule(Generic[T]):
    code: str
    check: Callable[[T, ValidationContext], bool]
    message: Callable[[T, ValidationContext], str] = _default_message
    context: Callable[[T, ValidationContext], Mapping[str, Any]] = _default_context
    severity: Severity = Severity.ERROR
    field: str | None = None


def run_rules(
    obj: T, rules: Sequence[Rule[T]], ctx: ValidationContext | None = None
) -> ValidationResult:
    """
    Applies all rules and returns a ValidationResult.
    Does NOT raise for data problems; callers decide policy.
    """
    if ctx is None:
        ctx = ValidationContext()
    result = ValidationResult()
    for rule in rules:
        try:
            ok = rule.check(obj, ctx)
        except Exception as e:
            # This is dev error in the rule itself.
            # We convert to an ERROR violation with a stable code.
            result.add(
                Violation(
                    code=f"VALIDATOR_EXCEPTION::{rule.code}",
                    message=f"Rule raised exception: {type(e).__name__}: {e}",
                    severity=Severity.ERROR,
                    field=rule.field,
                    context={**dict(rule.context(obj, ctx)), "rule": rule.code},
                )
            )
            continue
        if not ok:
            result.add(
                Violation(
                    code=rule.code,
                    message=rule.message(obj, ctx),
                    severity=rule.severity,
                    field=rule.field,
                    context=dict(rule.context(obj, ctx)),
                )
            )

    return result


# ---------------------------
# Shared helper predicates
# ---------------------------

Number = int | float | Decimal


def is_finite(x: Number) -> bool:
    try:
        if isinstance(x, Decimal):
            return x.is_finite()
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def is_non_negative(x: Number) -> bool:
    try:
        return is_finite(x) and x >= 0
    except (TypeError, ValueError):
        return False


def is_positive(x: Number) -> bool:
    try:
        return is_finite(x) and x > 0
    except (TypeError, ValueError):
        return False


def is_ohlc_sane(open_price: float, high: float, low: float, close: float) -> bool:
    # low <= {open, close} <= high and low <= high
    if low > high:
        return False
    return (low <= open_price <= high) and (low <= close <= high)


def is_aligned_to_minutes(ts: datetime, minutes: int) -> bool:
    if minutes <= 0:
        return False
    return (ts.minute % minutes == 0) and ts.second == 0 and ts.microsecond == 0


def is_strictly_increasing(seq: Sequence[T], key: Callable[[T], Any]) -> bool:
    if len(seq) < 2:
        return True
    prev = key(seq[0])
    for item in seq[1:]:
        cur = key(item)
        if cur <= prev:
            return False
        prev = cur
    return True
