from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction

# Alpaca /v1/corporate-actions returns one list per type; items carry no type field
# of their own. Map the list key to the provider type the parser understands.
ALPACA_LIST_KEY_TO_PROVIDER_TYPE: dict[str, str] = {
    "cash_dividends": "cash_dividend",
    "stock_dividends": "stock_dividend",
    "forward_splits": "forward_split",
    "reverse_splits": "reverse_split",
    "spin_offs": "spin_off",
    "cash_mergers": "cash_merger",
    "stock_mergers": "stock_merger",
    "name_changes": "name_change",
}

PROVIDER_TYPE_TO_ACTION_TYPE: dict[str, CorporateActionType] = {
    "cash_dividend": CorporateActionType.CASH_DIVIDEND,
    "stock_dividend": CorporateActionType.STOCK_DIVIDEND,
    "forward_split": CorporateActionType.SPLIT_FORWARD,
    "reverse_split": CorporateActionType.SPLIT_REVERSE,
    "spin_off": CorporateActionType.SPINOFF,
    "cash_merger": CorporateActionType.MERGER_CASH,
    "stock_merger": CorporateActionType.MERGER_STOCK,
    "name_change": CorporateActionType.NAME_CHANGE,
}

# Alpaca names the affected symbol differently per type: mergers use the acquiree,
# spin-offs the source company, name changes the old symbol.
_SYMBOL_FIELDS = ("symbol", "acquiree_symbol", "source_symbol", "old_symbol")
_EFFECTIVE_DATE_FIELDS = ("ex_date", "effective_date", "process_date")
_NEW_SYMBOL_FIELDS = ("new_symbol", "acquirer_symbol")
_CASH_FIELDS = ("cash", "rate")


class CorporateActionNormalizationService:
    @staticmethod
    def parse_alpaca_corporate_action(
        raw: dict,
        provider_type: str | None = None,
    ) -> CorporateAction:
        """Normalize one Alpaca corporate-action item.

        ``provider_type`` is the Alpaca list the item came from (``cash_dividend``,
        ``forward_split``, …). When omitted, an explicit ``ca_type``/``type`` field on
        the item is used instead.
        """
        resolved_type = provider_type or raw.get("ca_type") or raw.get("type")
        if not isinstance(resolved_type, str):
            raise ValueError("Corporate action missing valid 'type' field")

        action_type = PROVIDER_TYPE_TO_ACTION_TYPE.get(resolved_type)
        if action_type is None:
            raise ValueError(f"Unsupported corporate action type: {resolved_type}")

        action_id = raw.get("id")
        if action_id is None or (isinstance(action_id, str) and action_id.strip() == ""):
            raise ValueError("Corporate action missing required field: id")

        symbol = CorporateActionNormalizationService._first_present(raw, _SYMBOL_FIELDS)
        if symbol is None:
            raise ValueError("Corporate action missing required field: symbol")

        effective_date = CorporateActionNormalizationService._first_present(
            raw, _EFFECTIVE_DATE_FIELDS
        )
        if effective_date is None:
            raise ValueError("Corporate action missing required field: ex_date")

        split_ratio = None
        if action_type in {
            CorporateActionType.SPLIT_FORWARD,
            CorporateActionType.SPLIT_REVERSE,
        }:
            split_ratio = CorporateActionNormalizationService._parse_split_ratio(raw)

        cash_amount = None
        if action_type in {
            CorporateActionType.CASH_DIVIDEND,
            CorporateActionType.MERGER_CASH,
        }:
            cash_raw = CorporateActionNormalizationService._first_present(raw, _CASH_FIELDS)
            if cash_raw is not None:
                try:
                    cash_amount = Decimal(str(cash_raw))
                except (InvalidOperation, TypeError) as exc:
                    raise ValueError("Corporate action has invalid cash amount") from exc

        new_symbol = CorporateActionNormalizationService._first_present(raw, _NEW_SYMBOL_FIELDS)

        return CorporateAction(
            action_id=str(action_id),
            symbol=str(symbol).upper(),
            action_type=action_type,
            effective_date=effective_date,
            announced_date=raw.get("declaration_date"),
            record_date=raw.get("record_date"),
            payable_date=raw.get("payable_date"),
            split_ratio=split_ratio,
            cash_amount=cash_amount,
            currency=raw.get("currency", "USD"),
            new_symbol=str(new_symbol).upper() if new_symbol is not None else "",
            source="alpaca",
            ingested_at=datetime.now(UTC),
            metadata={**raw, "provider_type": resolved_type},
        )

    @staticmethod
    def _first_present(raw: dict, fields: tuple[str, ...]) -> object | None:
        for field_name in fields:
            value: object = raw.get(field_name)
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            return value
        return None

    @staticmethod
    def _parse_split_ratio(raw: dict) -> Decimal | None:
        old_shares = raw.get("old_rate")
        new_shares = raw.get("new_rate")

        if old_shares is None or new_shares is None:
            return None

        try:
            old_decimal = Decimal(str(old_shares))
            new_decimal = Decimal(str(new_shares))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Corporate action has invalid split rate fields") from exc

        if old_decimal == 0:
            raise ValueError("Corporate action old_rate cannot be zero")

        ratio = (new_decimal / old_decimal).normalize()
        # normalize() renders 10 as 1E+1; keep whole ratios as plain integers.
        if ratio == ratio.to_integral_value():
            return ratio.quantize(Decimal(1))
        return ratio
