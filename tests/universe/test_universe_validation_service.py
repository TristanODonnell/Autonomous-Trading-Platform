from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
)
from tests.utilities.factories import make_universe_snapshot


class FakeExecuteResult:
    def __init__(self, scalar_rows=None, rows=None) -> None:
        self._scalar_rows = scalar_rows if scalar_rows is not None else []
        self._rows = rows if rows is not None else []

    def scalars(self) -> FakeExecuteResult:
        return self

    def all(self):
        if self._rows:
            return self._rows
        return self._scalar_rows


class FakeSession:
    def __init__(self) -> None:
        self.dataset_symbols: list[str] = []
        self.coverage_rows: list[tuple[str, int]] = []
        self.executed: list[tuple[object, object | None]] = []

    def execute(self, stmt, params=None):
        self.executed.append((stmt, params))

        stmt_text = str(stmt)

        if "SELECT DISTINCT symbol" in stmt_text:
            return FakeExecuteResult(scalar_rows=self.dataset_symbols)

        return FakeExecuteResult(rows=self.coverage_rows)


class TestUniverseValidationService:
    def test_validate_reports_missing_symbols_with_descriptive_message(self) -> None:
        session = FakeSession()
        service = UniverseValidationService(session=session)
        snapshot = make_universe_snapshot(symbols=[])

        result = service.validate(snapshot)

        assert result.ok is False
        assert "UniverseSnapshot must contain at least one symbol" in result.errors

    def test_find_symbols_missing_from_dataset_identifies_missing_tickers(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)

        missing = service._find_symbols_missing_from_dataset(["AAPL", "MSFT", "NVDA"])

        assert missing == ["MSFT", "NVDA"]

    def test_validate_reports_symbols_missing_from_dataset(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)
        snapshot = make_universe_snapshot(symbols=["AAPL", "MSFT", "NVDA"])

        result = service.validate(snapshot)

        assert result.ok is False
        assert "UniverseSnapshot contains symbols missing from dataset: MSFT, NVDA" in result.errors

    def test_validate_reports_insufficient_recent_bar_coverage(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL", "MSFT"]
        session.coverage_rows = [
            ("AAPL", 9),
            ("MSFT", 5),
        ]
        service = UniverseValidationService(session=session)

        snapshot = make_universe_snapshot(
            symbols=["AAPL", "MSFT"],
            criteria={
                "lookback_days": 10,
                "selection_timestamp": "2025-01-15T14:30:00+00:00",
            },
        )

        result = service.validate(snapshot)

        assert result.ok is False
        assert (
            "UniverseSnapshot contains symbols with insufficient recent coverage: MSFT"
            in result.errors
        )

    def test_validate_allows_sufficient_recent_bar_coverage(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL", "MSFT"]
        session.coverage_rows = [
            ("AAPL", 9),
            ("MSFT", 8),
        ]
        service = UniverseValidationService(session=session)

        snapshot = make_universe_snapshot(
            symbols=["AAPL", "MSFT"],
            criteria={
                "lookback_days": 10,
                "selection_timestamp": "2025-01-15T14:30:00+00:00",
            },
        )

        result = service.validate(snapshot)

        assert result.ok is True
        assert result.errors == []

    def test_validate_reports_invalid_coverage_criteria_inputs(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)

        snapshot = make_universe_snapshot(
            symbols=["AAPL"],
            criteria={
                "lookback_days": "not-an-int",
                "selection_timestamp": "not-a-datetime",
            },
        )

        result = service.validate(snapshot)

        assert result.ok is False
        assert (
            "UniverseSnapshot criteria has invalid lookback_days or selection_timestamp"
            in result.errors
        )

    def test_format_rule_errors_includes_code_field_and_message(self) -> None:
        session = FakeSession()
        service = UniverseValidationService(session=session)

        rule_result = SimpleNamespace(
            violations=[
                SimpleNamespace(
                    code="INVALID_FIELD",
                    field="symbols",
                    message="symbols must be unique",
                ),
                SimpleNamespace(
                    code="BAD_SOURCE",
                    field=None,
                    message="source is invalid",
                ),
            ]
        )

        formatted = service._format_rule_errors(rule_result)

        assert formatted == [
            "INVALID_FIELD [symbols]: symbols must be unique",
            "BAD_SOURCE: source is invalid",
        ]

    def test_validate_propagates_contract_rule_violations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL", "MSFT"]
        service = UniverseValidationService(session=session)
        snapshot = make_universe_snapshot(symbols=["AAPL", "MSFT"])

        fake_rule_result = SimpleNamespace(
            violations=[
                SimpleNamespace(
                    code="DUPLICATE_SYMBOL",
                    field="symbols",
                    message="duplicate symbols are not allowed",
                )
            ]
        )

        monkeypatch.setattr(
            "autonomous_trading_platform.universe.services.universe_validation_service.run_rules",
            lambda snapshot_arg, rules_arg: fake_rule_result,
        )

        result = service.validate(snapshot)

        assert result.ok is False
        assert "DUPLICATE_SYMBOL [symbols]: duplicate symbols are not allowed" in result.errors

    def test_validate_accumulates_multiple_error_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        session.coverage_rows = [("AAPL", 3)]
        service = UniverseValidationService(session=session)

        snapshot = make_universe_snapshot(
            symbols=["AAPL", "MSFT"],
            criteria={
                "lookback_days": 10,
                "selection_timestamp": "2025-01-15T14:30:00+00:00",
            },
        )

        fake_rule_result = SimpleNamespace(
            violations=[
                SimpleNamespace(
                    code="BAD_NOTES",
                    field="notes",
                    message="notes exceeded limit",
                )
            ]
        )

        monkeypatch.setattr(
            "autonomous_trading_platform.universe.services.universe_validation_service.run_rules",
            lambda snapshot_arg, rules_arg: fake_rule_result,
        )

        result = service.validate(snapshot)

        assert result.ok is False
        assert "BAD_NOTES [notes]: notes exceeded limit" in result.errors
        assert "UniverseSnapshot contains symbols missing from dataset: MSFT" in result.errors
        assert (
            "UniverseSnapshot contains symbols with insufficient recent coverage: AAPL, MSFT"
            in result.errors
        )

    def test_validate_rejects_non_positive_lookback_days_with_descriptive_error(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)

        snapshot = make_universe_snapshot(
            symbols=["AAPL"],
            criteria={
                "lookback_days": 0,
                "selection_timestamp": "2025-01-15T14:30:00+00:00",
            },
        )

        result = service.validate(snapshot)

        assert result.ok is False
        assert "lookback_days must be positive" in result.errors

    def test_validate_reports_missing_symbols_in_sorted_order(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)
        snapshot = make_universe_snapshot(symbols=["NVDA", "AAPL", "MSFT"])

        result = service.validate(snapshot)

        assert "UniverseSnapshot contains symbols missing from dataset: MSFT, NVDA" in result.errors
