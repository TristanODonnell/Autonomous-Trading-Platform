from datetime import UTC, date, datetime
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.corporate_action import CORPORATE_ACTION_RULES


def make_corporate_action(**overrides) -> CorporateAction:
    data = {
        "action_id": "ca_123",
        "symbol": "AAPL",
        "action_type": CorporateActionType.CASH_DIVIDEND,
        "effective_date": date.today(),
        "announced_date": None,
        "record_date": None,
        "payable_date": None,
        "split_ratio": None,
        "cash_amount": Decimal("1.25"),
        "currency": "USD",
        "new_symbol": "AAPL",
        "source": "alpaca",
        "ingested_at": datetime.now(UTC),
        "metadata": None,
    }

    data.update(overrides)

    return CorporateAction(**data)


def test_valid_corporate_action_passes():
    ca = make_corporate_action()

    result = run_rules(ca, CORPORATE_ACTION_RULES)

    assert result.ok
    assert result.violations == []


def test_ratio_or_amount_must_be_positive():
    ca = make_corporate_action(
        action_type=CorporateActionType.SPLIT_FORWARD,
        split_ratio=0.0,
    )

    result = run_rules(ca, CORPORATE_ACTION_RULES)

    assert not result.ok
    assert any(v.code == "RATIO_OR_AMOUNT_POSITIVE" for v in result.violations)


def test_split_ratio_cannot_be_one_for_splits():
    ca = make_corporate_action(action_type=CorporateActionType.SPLIT_FORWARD, split_ratio=1.0)

    result = run_rules(ca, CORPORATE_ACTION_RULES)

    assert not result.ok
    assert any(v.code == "RATIO_OR_AMOUNT_NOT_ONE_WHEN_SPLIT" for v in result.violations)


def test_new_symbol_required_for_name_change():
    ca = make_corporate_action(action_type=CorporateActionType.NAME_CHANGE, new_symbol="")

    result = run_rules(ca, CORPORATE_ACTION_RULES)

    assert not result.ok
    assert any(v.code == "NEW_SYMBOL_PRESENT_WHEN_NAME_CHANGE" for v in result.violations)
