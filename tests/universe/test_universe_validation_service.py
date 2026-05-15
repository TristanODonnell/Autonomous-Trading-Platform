from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
)
from tests.utilities.factories import make_universe_member, make_universe_version


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
        self.executed: list[tuple[object, object | None]] = []

    def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        return FakeExecuteResult(scalar_rows=self.dataset_symbols)


class TestUniverseValidationService:
    def test_validate_fails_with_no_members(self) -> None:
        session = FakeSession()
        service = UniverseValidationService(session=session)
        version = make_universe_version()

        result = service.validate(version, [])

        assert result.ok is False
        assert "UniverseVersion must have at least one member symbol" in result.errors

    def test_validate_fails_with_duplicate_symbols(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)
        version = make_universe_version()
        members = [
            make_universe_member(symbol="AAPL"),
            make_universe_member(symbol="AAPL"),
        ]

        result = service.validate(version, members)

        assert result.ok is False
        assert "duplicate symbols" in result.errors[0]

    def test_validate_passes_with_all_symbols_in_dataset(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL", "MSFT"]
        service = UniverseValidationService(session=session)
        version = make_universe_version()
        members = [
            make_universe_member(symbol="AAPL"),
            make_universe_member(symbol="MSFT"),
        ]

        result = service.validate(version, members)

        assert result.ok is True
        assert result.errors == []

    def test_validate_reports_symbols_missing_from_dataset(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)
        version = make_universe_version()
        members = [
            make_universe_member(symbol="AAPL"),
            make_universe_member(symbol="MSFT"),
            make_universe_member(symbol="NVDA"),
        ]

        result = service.validate(version, members)

        assert result.ok is False
        assert "UniverseVersion contains symbols missing from dataset: MSFT, NVDA" in result.errors

    def test_validate_propagates_version_rule_violations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)
        version = make_universe_version()
        members = [make_universe_member(symbol="AAPL")]

        fake_rule_result = SimpleNamespace(
            violations=[
                SimpleNamespace(
                    code="STATUS_VALID",
                    field="status",
                    message="invalid status",
                )
            ]
        )

        monkeypatch.setattr(
            "autonomous_trading_platform.universe.services.universe_validation_service.run_rules",
            lambda _obj, _rules: fake_rule_result,
        )

        result = service.validate(version, members)

        assert result.ok is False
        assert "STATUS_VALID [status]: invalid status" in result.errors

    def test_find_symbols_missing_from_dataset_returns_missing(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)

        missing = service._find_symbols_missing_from_dataset(["AAPL", "MSFT", "NVDA"])

        assert missing == ["MSFT", "NVDA"]

    def test_validate_missing_symbols_sorted_in_error_message(self) -> None:
        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)
        version = make_universe_version()
        members = [
            make_universe_member(symbol="NVDA"),
            make_universe_member(symbol="AAPL"),
            make_universe_member(symbol="MSFT"),
        ]

        result = service.validate(version, members)

        assert "UniverseVersion contains symbols missing from dataset: MSFT, NVDA" in result.errors

    def test_format_rule_errors_includes_code_field_and_message(self) -> None:
        session = FakeSession()
        service = UniverseValidationService(session=session)

        rule_result = SimpleNamespace(
            violations=[
                SimpleNamespace(code="INVALID_FIELD", field="symbol", message="bad symbol"),
                SimpleNamespace(code="BAD_SOURCE", field=None, message="source is invalid"),
            ]
        )

        formatted = service._format_rule_errors(rule_result)

        assert formatted == [
            "INVALID_FIELD [symbol]: bad symbol",
            "BAD_SOURCE: source is invalid",
        ]

    def test_validate_version_row_adapter_works(self) -> None:
        from datetime import UTC, datetime

        from autonomous_trading_platform.contracts.common.enums import (
            UniverseSource,
            UniverseStatus,
        )
        from autonomous_trading_platform.storage.sor.models.universe_versions import (
            UniverseMember as UniverseMemberRow,
        )
        from autonomous_trading_platform.storage.sor.models.universe_versions import (
            UniverseVersion as UniverseVersionRow,
        )

        session = FakeSession()
        session.dataset_symbols = ["AAPL"]
        service = UniverseValidationService(session=session)

        now = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        version_row = UniverseVersionRow(
            universe_version_id="v-adapt-01",
            name="test",
            source=UniverseSource.CUSTOM,
            created_at=now,
            effective_from=now,
            effective_to=None,
            status=UniverseStatus.ACTIVE,
            rebalance_reason=None,
            config_hash="abc123",
        )
        member_row = UniverseMemberRow(
            universe_version_id="v-adapt-01",
            symbol="AAPL",
            rank=0,
            score=None,
            included_reason=None,
            excluded_reason=None,
            liquidity_metrics_json=None,
            quality_metrics_json=None,
        )

        result = service.validate_version_row(version_row, [member_row])

        assert result.ok is True
