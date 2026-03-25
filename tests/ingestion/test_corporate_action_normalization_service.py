from __future__ import annotations

from datetime import date

import pytest

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_normalization_service import (
    CorporateActionNormalizationService,
)


def make_raw_action(
    *,
    action_id: str = "ca-123",
    symbol: str = "AAPL",
    provider_type: str | None = "cash_dividend",
    ex_date: str = "2025-01-15",
    declaration_date: str | None = "2025-01-01",
    record_date: str | None = "2025-01-10",
    payable_date: str | None = "2025-01-20",
    old_rate: str | None = None,
    new_rate: str | None = None,
    cash: str | None = None,
    currency: str = "USD",
    new_symbol: str | None = None,
) -> dict:
    raw = {
        "id": action_id,
        "symbol": symbol,
        "ex_date": ex_date,
        "declaration_date": declaration_date,
        "record_date": record_date,
        "payable_date": payable_date,
        "currency": currency,
    }

    if provider_type is not None:
        raw["ca_type"] = provider_type

    if old_rate is not None:
        raw["old_rate"] = old_rate
    if new_rate is not None:
        raw["new_rate"] = new_rate
    if cash is not None:
        raw["cash"] = cash
    if new_symbol is not None:
        raw["new_symbol"] = new_symbol

    return raw


class TestCorporateActionNormalizationService:
    @pytest.mark.parametrize(
        ("provider_type", "expected_action_type"),
        [
            ("cash_dividend", CorporateActionType.CASH_DIVIDEND),
            ("stock_dividend", CorporateActionType.STOCK_DIVIDEND),
            ("forward_split", CorporateActionType.SPLIT_FORWARD),
            ("reverse_split", CorporateActionType.SPLIT_REVERSE),
            ("spin_off", CorporateActionType.SPINOFF),
            ("cash_merger", CorporateActionType.MERGER_CASH),
            ("stock_merger", CorporateActionType.MERGER_STOCK),
            ("name_change", CorporateActionType.NAME_CHANGE),
        ],
    )
    def test_parse_alpaca_corporate_action_maps_provider_types_to_internal_types(
        self,
        provider_type: str,
        expected_action_type: CorporateActionType,
    ) -> None:
        raw = make_raw_action(provider_type=provider_type)

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == expected_action_type
        assert action.source == "alpaca"
        assert action.action_id == "ca-123"
        assert action.symbol == "AAPL"

    def test_parse_alpaca_corporate_action_accepts_fallback_type_key(self) -> None:
        raw = make_raw_action(provider_type=None)
        raw["type"] = "cash_dividend"

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == CorporateActionType.CASH_DIVIDEND

    @pytest.mark.parametrize("bad_type", [None, 123, [], {}])
    def test_parse_alpaca_corporate_action_rejects_missing_or_non_string_type(
        self,
        bad_type: object,
    ) -> None:
        raw = make_raw_action(provider_type=None)

        if bad_type is not None:
            raw["ca_type"] = bad_type

        with pytest.raises(ValueError, match="missing valid 'type' field"):
            CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

    def test_parse_alpaca_corporate_action_rejects_unsupported_type(self) -> None:
        raw = make_raw_action(provider_type="rights_offering")

        with pytest.raises(ValueError, match="Unsupported corporate action type"):
            CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

    def test_parse_alpaca_corporate_action_normalizes_forward_split_ratio(self) -> None:
        raw = make_raw_action(
            provider_type="forward_split",
            old_rate="1",
            new_rate="4",
        )

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == CorporateActionType.SPLIT_FORWARD
        assert str(action.split_ratio) == "4"
        assert action.cash_amount is None

    def test_parse_alpaca_corporate_action_normalizes_reverse_split_ratio(self) -> None:
        raw = make_raw_action(
            provider_type="reverse_split",
            old_rate="5",
            new_rate="1",
        )

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == CorporateActionType.SPLIT_REVERSE
        assert str(action.split_ratio) == "0.2"
        assert action.cash_amount is None

    def test_parse_alpaca_corporate_action_leaves_split_ratio_none_when_split_rates_missing(
        self,
    ) -> None:
        raw = make_raw_action(provider_type="forward_split")

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.split_ratio is None

    def test_parse_alpaca_corporate_action_normalizes_cash_amount_for_cash_dividend(
        self,
    ) -> None:
        raw = make_raw_action(
            provider_type="cash_dividend",
            cash="1.23",
        )

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == CorporateActionType.CASH_DIVIDEND
        assert str(action.cash_amount) == "1.23"

    def test_parse_alpaca_corporate_action_normalizes_cash_amount_for_cash_merger(
        self,
    ) -> None:
        raw = make_raw_action(
            provider_type="cash_merger",
            cash="42.50",
        )

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == CorporateActionType.MERGER_CASH
        assert str(action.cash_amount) == "42.50"

    def test_parse_alpaca_corporate_action_does_not_set_cash_amount_for_non_cash_actions(
        self,
    ) -> None:
        raw = make_raw_action(
            provider_type="stock_dividend",
            cash="9.99",
        )

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.action_type == CorporateActionType.STOCK_DIVIDEND
        assert action.cash_amount is None

    def test_parse_alpaca_corporate_action_normalizes_date_fields_correctly(self) -> None:
        raw = make_raw_action(
            provider_type="cash_dividend",
            ex_date="2025-01-15",
            declaration_date="2025-01-01",
            record_date="2025-01-10",
            payable_date="2025-01-20",
        )

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert (
            action.effective_date == date(2025, 1, 15) or str(action.effective_date) == "2025-01-15"
        )
        assert (
            action.announced_date == date(2025, 1, 1) or str(action.announced_date) == "2025-01-01"
        )
        assert action.record_date == date(2025, 1, 10) or str(action.record_date) == "2025-01-10"
        assert action.payable_date == date(2025, 1, 20) or str(action.payable_date) == "2025-01-20"

    def test_parse_alpaca_corporate_action_defaults_currency_and_new_symbol(self) -> None:
        raw = make_raw_action(
            provider_type="name_change",
            new_symbol=None,
        )
        raw.pop("currency", None)

        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)

        assert action.currency == "USD"
        assert action.new_symbol == ""

    def test_parse_alpaca_corporate_action_rejects_missing_required_provider_fields_with_clear_error(
        self,
    ) -> None:
        raw = make_raw_action(provider_type="cash_dividend")
        raw.pop("symbol")

        with pytest.raises(ValueError, match="missing required field"):
            CorporateActionNormalizationService.parse_alpaca_corporate_action(raw)
