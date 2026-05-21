"""Tests for SurvivorshipValidationService."""

from __future__ import annotations

from datetime import date

from autonomous_trading_platform.research.validation.survivorship_validation import (
    SurvivorshipValidationService,
)


def test_no_scope_warns_but_safe():
    svc = SurvivorshipValidationService()
    result = svc.validate(
        universe_scope=None,
        experiment_start=date(2022, 1, 1),
        experiment_end=date(2023, 1, 1),
    )
    assert result.is_safe  # no scope = unknown, not unsafe
    assert any("skipped" in w.lower() for w in result.warnings)


def test_no_leaked_symbols():
    svc = SurvivorshipValidationService()
    result = svc.validate(
        universe_scope=None,
        experiment_start=date(2022, 1, 1),
        experiment_end=date(2023, 1, 1),
        symbols=["AAPL", "MSFT"],
        known_future_symbols={"NVDA"},  # NVDA not in experiment symbols
    )
    assert result.is_safe
    assert result.leaked_symbol_count == 0


def test_leaked_symbols_detected():
    svc = SurvivorshipValidationService()
    result = svc.validate(
        universe_scope=None,
        experiment_start=date(2022, 1, 1),
        experiment_end=date(2023, 1, 1),
        symbols=["AAPL", "MSFT", "NVDA"],
        known_future_symbols={"NVDA"},  # NVDA is a future symbol in the experiment!
    )
    assert not result.is_safe
    assert result.leaked_symbol_count == 1
    assert "NVDA" in result.leaked_symbols
    assert len(result.errors) > 0


def test_guard_failure_marks_unsafe():
    class _MockGuard:
        def validate_experiment_scope(self, scope, start, end):
            raise RuntimeError("No PIT anchor")

        def validate_symbols_not_future_leaked(self, symbols, ts, known):
            pass

    svc = SurvivorshipValidationService(survivorship_guard=_MockGuard())

    class _FakeScope:
        mode = "ACTIVE_AS_OF_START_DATE"
        universe_version_id = None
        effective_timestamp = None
        replay_rotation_enabled = False
        replay_transitions = []

    result = svc.validate(
        universe_scope=_FakeScope(),
        experiment_start=date(2020, 1, 1),
        experiment_end=date(2021, 1, 1),
    )
    assert not result.is_safe
    assert len(result.errors) > 0


def test_fold_windows_no_history_warns():
    svc = SurvivorshipValidationService(universe_history_service=None)
    result = svc.validate(
        universe_scope=None,
        experiment_start=date(2022, 1, 1),
        experiment_end=date(2023, 1, 1),
        fold_windows=[(0, date(2022, 3, 1), ["AAPL"])],
    )
    assert any("fold-level" in w.lower() for w in result.warnings)


def test_guard_safe_scope():
    class _SafeGuard:
        def validate_experiment_scope(self, scope, start, end):
            pass  # no exception = safe

        def validate_symbols_not_future_leaked(self, symbols, ts, known):
            pass

    svc = SurvivorshipValidationService(survivorship_guard=_SafeGuard())

    class _SafeScope:
        mode = "ACTIVE_AS_OF_START_DATE"
        universe_version_id = "v1"
        effective_timestamp = "2022-01-01"
        replay_rotation_enabled = False
        replay_transitions = []

    result = svc.validate(
        universe_scope=_SafeScope(),
        experiment_start=date(2022, 1, 1),
        experiment_end=date(2023, 1, 1),
    )
    assert result.is_safe
    assert len(result.errors) == 0
