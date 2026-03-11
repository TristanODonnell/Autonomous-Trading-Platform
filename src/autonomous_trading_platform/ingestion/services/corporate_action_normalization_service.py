from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction


def parse_alpaca_corporate_action(raw: dict) -> CorporateAction:
    provider_type = raw.get("ca_type") or raw.get("type")

    action_type_map = {
        "cash_dividend": CorporateActionType.CASH_DIVIDEND,
        "stock_dividend": CorporateActionType.STOCK_DIVIDEND,
        "forward_split": CorporateActionType.SPLIT_FORWARD,
        "reverse_split": CorporateActionType.SPLIT_REVERSE,
        "spin_off": CorporateActionType.SPINOFF,
        "cash_merger": CorporateActionType.MERGER_CASH,
        "stock_merger": CorporateActionType.MERGER_STOCK,
        "name_change": CorporateActionType.NAME_CHANGE,
    }
    provider_type = raw.get("type")

    if not isinstance(provider_type, str):
        raise ValueError("Corporate action missing valid 'type' field")

    action_type = action_type_map.get(provider_type)

    if action_type is None:
        raise ValueError(f"Unsupported corporate action type: {provider_type}")

    split_ratio = None
    if action_type in {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
    }:
        old_shares = raw.get("old_rate")
        new_shares = raw.get("new_rate")

        if old_shares is not None and new_shares is not None:
            split_ratio = Decimal(str(new_shares)) / Decimal(str(old_shares))

    cash_amount = None

    if (
        action_type
        in {
            CorporateActionType.CASH_DIVIDEND,
            CorporateActionType.MERGER_CASH,
        }
        and raw.get("cash") is not None
    ):
        cash_amount = Decimal(str(raw["cash"]))

    return CorporateAction(
        action_id=str(raw["id"]),
        symbol=raw["symbol"],
        action_type=action_type,
        effective_date=raw["ex_date"],
        announced_date=raw.get("declaration_date"),
        record_date=raw.get("record_date"),
        payable_date=raw.get("payable_date"),
        split_ratio=split_ratio,
        cash_amount=cash_amount,
        currency=raw.get("currency", "USD"),
        new_symbol=raw.get("new_symbol", ""),
        source="alpaca",
        ingested_at=datetime.now(UTC),
        metadata=raw,
    )
