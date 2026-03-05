from __future__ import annotations

from datetime import datetime

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction

from .core import Rule, is_aligned_to_minutes, is_positive

CORPORATE_ACTION_RULES: list[Rule[CorporateAction]] = [
    # effective_date is present and valid date.
    Rule(
        code="EFFECTIVE_DATE_PRESENT_AND_VALID",
        field="effective_date",
        check=lambda ca, _ctx: (
            isinstance(ca.effective_date, datetime) and is_aligned_to_minutes(ca.effective_date, 5)
        ),
        message=lambda ca, _ctx: "effective date must be present and valid",
    ),
    # ratio_or_amount > 0.
    Rule(
        code="RATIO_OR_AMOUNT_POSITIVE",
        field="ratio_or_amount",
        check=lambda ca, _ctx: is_positive(ca.ratio_or_amount),
        message=lambda ca, _ctx: "ratio_or_amount must be positive",
    ),
    # If type is a split: ratio_or_amount != 1.0.
    Rule(
        code="IF_ACTIONTYPE_SPLIT_RATIO_OR_AMOUNT_NE_1.0",
        field="ratio_or_amount",
        check=lambda ca, _ctx: (
            (
                ca.CorporateActionType != CorporateActionType.SPLIT_FORWARD
                and ca.CorporateActionType != CorporateActionType.SPLIT_REVERSE
            )
            or (ca.ratio_or_amount != 1.0)
        ),
        message=lambda ca, _ctx: "When type is a split, ratio_or_amount must not equal 1.0",
    ),
    # If type="name_change" then new_symbol must be present.
    Rule(
        code="IF_ACTIONTYPE_NAME_CHANGE_THEN_NEW_SYMBOL_MUST_EXIST",
        field="action_type",
        check=lambda ca, _ctx: (
            (ca.CorporateActionType != CorporateActionType.NAME_CHANGE)
            or isinstance(ca.new_symbol, str)
        ),
        message=lambda ca, _ctx: "When type is name_change, new_symbol must not be empty",
    ),
]
