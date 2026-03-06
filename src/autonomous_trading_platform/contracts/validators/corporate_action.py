from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction

from .core import Rule, is_positive

CORPORATE_ACTION_RULES: list[Rule[CorporateAction]] = [
    Rule(
        code="RATIO_OR_AMOUNT_POSITIVE",
        field="split_ratio",
        check=lambda ca, _ctx: (
            ca.action_type
            not in {
                CorporateActionType.SPLIT_FORWARD,
                CorporateActionType.SPLIT_REVERSE,
            }
            or (ca.split_ratio is not None and is_positive(ca.split_ratio))
        ),
        message=lambda ca, _ctx: "when action_type is a split, split_ratio must be > 0",
    ),
    Rule(
        code="RATIO_OR_AMOUNT_NOT_ONE_WHEN_SPLIT",
        field="split_ratio",
        check=lambda ca, _ctx: (
            ca.action_type
            not in {
                CorporateActionType.SPLIT_FORWARD,
                CorporateActionType.SPLIT_REVERSE,
            }
            or ca.split_ratio != 1.0
        ),
        message=lambda ca, _ctx: "when action_type is a split, split_ratio must not equal 1.0",
    ),
    Rule(
        code="NEW_SYMBOL_PRESENT_WHEN_NAME_CHANGE",
        field="new_symbol",
        check=lambda ca, _ctx: (
            ca.action_type != CorporateActionType.NAME_CHANGE
            or (isinstance(ca.new_symbol, str) and ca.new_symbol.strip() != "")
        ),
        message=lambda ca, _ctx: (
            "when action_type is NAME_CHANGE, new_symbol must be present and non-empty"
        ),
    ),
]
