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


# ---------------------------------------------------------------------------
# Real Alpaca /v1/corporate-actions payloads (captured 2026-10-02). Items carry no
# type field: the list key is the type, passed as ``provider_type``.
# ---------------------------------------------------------------------------

NVDA_FORWARD_SPLIT = {
    "cusip": "67066G104",
    "due_bill_redemption_date": "2024-06-10",
    "ex_date": "2024-06-10",
    "id": "50199fac-0af8-43ef-9846-eaf64c6d322d",
    "new_rate": 10,
    "old_rate": 1,
    "payable_date": "2024-06-10",
    "process_date": "2024-06-10",
    "record_date": "2024-06-07",
    "symbol": "NVDA",
}
AAPL_CASH_DIVIDEND = {
    "cusip": "037833100",
    "ex_date": "2024-02-09",
    "foreign": False,
    "id": "35849a16-e94e-4f9a-b66f-a1333d6289af",
    "payable_date": "2024-02-15",
    "process_date": "2024-02-15",
    "rate": 0.24,
    "record_date": "2024-02-12",
    "special": False,
    "symbol": "AAPL",
}
ATRA_REVERSE_SPLIT = {
    "ex_date": "2024-06-20",
    "id": "446f18f3-92fc-42a8-8b93-4700f06bc8e0",
    "new_cusip": "046513206",
    "new_rate": 1,
    "old_cusip": "046513107",
    "old_rate": 25,
    "payable_date": "2024-06-20",
    "process_date": "2024-06-20",
    "record_date": "2024-06-20",
    "symbol": "ATRA",
}
PXD_XOM_STOCK_MERGER = {
    "acquiree_cusip": "723787107",
    "acquiree_rate": 1,
    "acquiree_symbol": "PXD",
    "acquirer_cusip": "30231G102",
    "acquirer_rate": 2.3234,
    "acquirer_symbol": "XOM",
    "effective_date": "2024-05-03",
    "id": "db28e8a7-66f6-4c76-ba9c-e87b3060edcd",
    "payable_date": "2024-05-03",
    "process_date": "2024-05-03",
}
CASH_MERGER = {
    "acquiree_cusip": "358CVR025",
    "acquiree_symbol": "358CVR025",
    "effective_date": "2024-06-04",
    "id": "8bf3ce03-af62-43d0-9985-c9757d735ede",
    "payable_date": "2024-06-04",
    "process_date": "2024-06-04",
    "rate": 0.01895669,
}
SPIN_OFF = {
    "ex_date": "2024-06-03",
    "id": "48ec8707-a183-421f-83eb-51f39ed6d36d",
    "new_cusip": "007975113",
    "new_rate": 0.47698,
    "new_symbol": "007975113",
    "process_date": "2024-06-03",
    "record_date": "2024-05-31",
    "source_cusip": "007975600",
    "source_rate": 1,
    "source_symbol": "AEZS",
}
NAME_CHANGE = {
    "id": "76d6dc2b-ae42-4294-9bc0-1305650fde0d",
    "new_cusip": "06777U101",
    "new_symbol": "BNED",
    "old_cusip": "067BAS012",
    "old_symbol": "067BAS012",
    "process_date": "2024-06-11",
}
STOCK_DIVIDEND = {
    "cusip": "009126202",
    "ex_date": "2024-06-24",
    "id": "57ae88b6-6022-4c4b-be1b-81e544dd49b3",
    "payable_date": "2024-07-01",
    "process_date": "2024-06-24",
    "rate": 1.1,
    "record_date": "2024-06-24",
    "symbol": "AIQUY",
}


class TestRealAlpacaPayloads:
    def test_forward_split_from_list_key(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            NVDA_FORWARD_SPLIT, "forward_split"
        )
        assert action.action_type == CorporateActionType.SPLIT_FORWARD
        assert action.symbol == "NVDA"
        assert action.effective_date == date(2024, 6, 10)
        assert action.record_date == date(2024, 6, 7)
        assert str(action.split_ratio) == "10"
        assert action.cash_amount is None
        assert action.action_id == "50199fac-0af8-43ef-9846-eaf64c6d322d"
        assert action.metadata is not None
        assert action.metadata["provider_type"] == "forward_split"

    def test_reverse_split_from_list_key(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            ATRA_REVERSE_SPLIT, "reverse_split"
        )
        assert action.action_type == CorporateActionType.SPLIT_REVERSE
        assert str(action.split_ratio) == "0.04"

    def test_cash_dividend_reads_rate_as_cash_amount(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            AAPL_CASH_DIVIDEND, "cash_dividend"
        )
        assert action.action_type == CorporateActionType.CASH_DIVIDEND
        assert action.symbol == "AAPL"
        assert action.effective_date == date(2024, 2, 9)
        assert action.payable_date == date(2024, 2, 15)
        assert str(action.cash_amount) == "0.24"
        assert action.currency == "USD"

    def test_stock_merger_uses_acquiree_symbol_and_effective_date(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            PXD_XOM_STOCK_MERGER, "stock_merger"
        )
        assert action.action_type == CorporateActionType.MERGER_STOCK
        assert action.symbol == "PXD"
        assert action.new_symbol == "XOM"
        assert action.effective_date == date(2024, 5, 3)
        assert action.split_ratio is None

    def test_cash_merger_reads_rate_as_cash_amount(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            CASH_MERGER, "cash_merger"
        )
        assert action.action_type == CorporateActionType.MERGER_CASH
        assert action.symbol == "358CVR025"
        assert str(action.cash_amount) == "0.01895669"

    def test_spin_off_uses_source_symbol(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            SPIN_OFF, "spin_off"
        )
        assert action.action_type == CorporateActionType.SPINOFF
        assert action.symbol == "AEZS"
        assert action.new_symbol == "007975113"
        assert action.effective_date == date(2024, 6, 3)

    def test_name_change_uses_old_symbol_and_process_date(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            NAME_CHANGE, "name_change"
        )
        assert action.action_type == CorporateActionType.NAME_CHANGE
        assert action.symbol == "067BAS012"
        assert action.new_symbol == "BNED"
        assert action.effective_date == date(2024, 6, 11)

    def test_stock_dividend_is_parsed_without_a_ratio(self) -> None:
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            STOCK_DIVIDEND, "stock_dividend"
        )
        assert action.action_type == CorporateActionType.STOCK_DIVIDEND
        assert action.split_ratio is None
        assert action.cash_amount is None
        assert action.metadata is not None
        assert action.metadata["rate"] == 1.1

    def test_real_item_without_provider_type_still_fails_clearly(self) -> None:
        with pytest.raises(ValueError, match="missing valid 'type' field"):
            CorporateActionNormalizationService.parse_alpaca_corporate_action(NVDA_FORWARD_SPLIT)

    def test_explicit_provider_type_wins_over_item_fields(self) -> None:
        raw = {**AAPL_CASH_DIVIDEND, "ca_type": "forward_split"}
        action = CorporateActionNormalizationService.parse_alpaca_corporate_action(
            raw, "cash_dividend"
        )
        assert action.action_type == CorporateActionType.CASH_DIVIDEND

    def test_unknown_list_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported corporate action type"):
            CorporateActionNormalizationService.parse_alpaca_corporate_action(
                {"id": "x", "symbol": "ZZZ", "process_date": "2024-06-13"}, "worthless_removal"
            )
