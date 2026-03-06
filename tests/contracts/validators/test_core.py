from datetime import UTC, datetime
from decimal import Decimal

from autonomous_trading_platform.contracts.validators.core import (
    Rule,
    ValidationContext,
    is_aligned_to_minutes,
    is_finite,
    is_non_negative,
    is_positive,
    run_rules,
)


def test_is_finite_valid_numbers():
    assert is_finite(1)
    assert is_finite(1.5)
    assert is_finite(Decimal("2.5"))


def test_is_finite_rejects_invalid_numbers():
    assert not is_finite(float("inf"))
    assert not is_finite(float("nan"))
    assert not is_finite(Decimal("NaN"))


def test_is_non_negative():
    assert is_non_negative(0)
    assert is_non_negative(5)
    assert not is_non_negative(-1)


def test_is_positive():
    assert is_positive(1)
    assert not is_positive(0)
    assert not is_positive(-1)


def test_is_aligned_to_minutes_valid():
    ts = datetime(2025, 1, 1, 10, 5, 0, tzinfo=UTC)
    assert is_aligned_to_minutes(ts, 5)


def test_is_aligned_to_minutes_invalid():
    ts = datetime(2025, 1, 1, 10, 6, 0, tzinfo=UTC)
    assert not is_aligned_to_minutes(ts, 5)


def test_run_rules_success():
    obj = {"x": 5}

    rules = [Rule(code="X_POSITIVE", check=lambda o, ctx: o["x"] > 0)]

    result = run_rules(obj, rules, ValidationContext())

    assert result.ok
    assert result.violations == []


def test_run_rules_detects_violation():
    obj = {"x": -1}

    rules = [Rule(code="X_POSITIVE", field="x", check=lambda o, ctx: o["x"] > 0)]

    result = run_rules(obj, rules)

    assert not result.ok
    assert len(result.violations) == 1
    assert result.violations[0].code == "X_POSITIVE"
    assert result.violations[0].field == "x"


def _broken_check(o, ctx) -> bool:
    raise ZeroDivisionError("boom")


def test_run_rules_handles_rule_exception():
    obj = {"x": 1}

    rules = [Rule(code="BROKEN_RULE", field="x", check=_broken_check)]

    result = run_rules(obj, rules)

    assert not result.ok
    assert result.violations[0].code == "VALIDATOR_EXCEPTION::BROKEN_RULE"
