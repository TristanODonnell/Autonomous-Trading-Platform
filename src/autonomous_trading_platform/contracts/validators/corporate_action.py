from __future__ import annotations

from datetime import date

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction

from .core import Rule, is_positive

CORPORATE_ACTION_RULES: list[Rule[CorporateAction]] = [
    Rule(
        code="EFFECTIVE_DATE_PRESENT_AND_VALID",
        field="effective_date",
        check=lambda ca, _ctx: isinstance(ca.effective_date, date),
        message=lambda ca, _ctx: "effective_date must be a valid date",
    ),
    Rule(
        code="RATIO_OR_AMOUNT_POSITIVE",
        field="ratio_or_amount",
        check=lambda ca, _ctx: is_positive(ca.ratio_or_amount),
        message=lambda ca, _ctx: "ratio_or_amount must be > 0",
    ),
    Rule(
        code="RATIO_OR_AMOUNT_NOT_ONE_WHEN_SPLIT",
        field="ratio_or_amount",
        check=lambda ca, _ctx: (
            (
                ca.type != CorporateActionType.SPLIT_FORWARD
                and ca.type != CorporateActionType.SPLIT_REVERSE
            )
            or ca.ratio_or_amount != 1.0
        ),
        message=lambda ca, _ctx: "when type is split, ratio_or_amount must not equal 1.0",
    ),
    Rule(
        code="NEW_SYMBOL_PRESENT_WHEN_NAME_CHANGE",
        field="new_symbol",
        check=lambda ca, _ctx: (
            ca.type != CorporateActionType.NAME_CHANGE
            or (isinstance(ca.new_symbol, str) and ca.new_symbol.strip() != "")
        ),
        message=lambda ca, _ctx: (
            "when type is name_change, new_symbol must be present and non-empty"
        ),
    ),
]
