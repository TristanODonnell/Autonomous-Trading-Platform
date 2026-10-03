"""The shared corporate-action rule (plan 5d, D3): splits change shares and cost, never
P&L; dividends are cash; fractional shares after a reverse split are paid in cash."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from autonomous_trading_platform.accounting.corporate_actions import (
    CorporateActionRuleError,
    apply_action,
    apply_split,
    dividend_cash,
    is_applicable,
    split_factor_before,
)
from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction


def _action(
    action_type: CorporateActionType,
    *,
    ratio: str | None = None,
    cash: str | None = None,
    symbol: str = "NVDA",
    action_id: str = "a1",
) -> CorporateAction:
    return CorporateAction(
        action_id=action_id,
        symbol=symbol,
        action_type=action_type,
        effective_date=date(2024, 6, 10),
        split_ratio=Decimal(ratio) if ratio is not None else None,
        cash_amount=Decimal(cash) if cash is not None else None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


class TestSplit:
    def test_forward_split_multiplies_shares_and_divides_cost_without_pnl(self) -> None:
        result = apply_split(Decimal("3"), Decimal("1207.185"), Decimal("10"))
        assert result.quantity == Decimal("30")
        assert result.avg_cost == Decimal("120.7185")
        assert result.quantity_delta == Decimal("27")
        assert result.fractional_shares == Decimal("0")
        assert result.cash_in_lieu == Decimal("0")
        assert result.realized_pnl == Decimal("0")
        # cost basis preserved
        assert result.quantity * result.avg_cost == Decimal("3") * Decimal("1207.185")

    def test_reverse_split_rounds_down_and_pays_the_remainder_in_cash(self) -> None:
        # 1-for-25 on 110 shares at $2: 4.4 post-split shares → 4 held, 0.4 paid.
        result = apply_split(
            Decimal("110"), Decimal("2"), Decimal("0.04"), cash_in_lieu_price=Decimal("55")
        )
        assert result.quantity == Decimal("4")
        assert result.avg_cost == Decimal("50")
        assert result.quantity_delta == Decimal("-106")
        assert result.fractional_shares == Decimal("0.4")
        assert result.cash_in_lieu == Decimal("22")  # 0.4 × 55
        assert result.realized_pnl == Decimal("2")  # 22 − 0.4 × 50
        # holdings + cash == old cost basis + realized
        assert result.quantity * result.avg_cost + result.cash_in_lieu == (
            Decimal("110") * Decimal("2") + result.realized_pnl
        )

    def test_reverse_split_without_a_price_pays_the_remainder_at_cost(self) -> None:
        result = apply_split(Decimal("110"), Decimal("2"), Decimal("0.04"))
        assert result.quantity == Decimal("4")
        assert result.cash_in_lieu == Decimal("20")  # 0.4 × 50
        assert result.realized_pnl == Decimal("0")

    def test_three_for_two_split_handles_odd_lots(self) -> None:
        result = apply_split(
            Decimal("5"), Decimal("30"), Decimal("1.5"), cash_in_lieu_price=Decimal("20")
        )
        assert result.quantity == Decimal("7")
        assert result.fractional_shares == Decimal("0.5")
        assert result.cash_in_lieu == Decimal("10")
        assert result.avg_cost == Decimal("20")

    def test_zero_position_stays_zero(self) -> None:
        result = apply_split(Decimal("0"), Decimal("100"), Decimal("10"))
        assert result.quantity == Decimal("0")
        assert result.cash_in_lieu == Decimal("0")

    @pytest.mark.parametrize("bad", ["0", "-2"])
    def test_rejects_non_positive_ratio(self, bad: str) -> None:
        with pytest.raises(CorporateActionRuleError):
            apply_split(Decimal("1"), Decimal("1"), Decimal(bad))

    def test_rejects_short_position(self) -> None:
        with pytest.raises(CorporateActionRuleError):
            apply_split(Decimal("-1"), Decimal("1"), Decimal("2"))


class TestDividend:
    def test_cash_is_shares_times_rate(self) -> None:
        assert dividend_cash(Decimal("41"), Decimal("0.24")) == Decimal("9.84")

    def test_zero_shares_pay_nothing(self) -> None:
        assert dividend_cash(Decimal("0"), Decimal("0.24")) == Decimal("0")


class TestApplyAction:
    def test_forward_split_action(self) -> None:
        adj = apply_action(
            _action(CorporateActionType.SPLIT_FORWARD, ratio="10"),
            quantity=Decimal("3"),
            avg_cost=Decimal("1207.185"),
        )
        assert adj.quantity_before == Decimal("3")
        assert adj.quantity_after == Decimal("30")
        assert adj.avg_cost_after == Decimal("120.7185")
        assert adj.cash_delta == Decimal("0")
        assert adj.realized_pnl == Decimal("0")
        assert adj.position_changed is True
        assert adj.quantity_delta == Decimal("27")

    def test_reverse_split_action_with_price(self) -> None:
        adj = apply_action(
            _action(CorporateActionType.SPLIT_REVERSE, ratio="0.04"),
            quantity=Decimal("110"),
            avg_cost=Decimal("2"),
            cash_in_lieu_price=Decimal("55"),
        )
        assert adj.quantity_after == Decimal("4")
        assert adj.cash_delta == Decimal("22")
        assert adj.fractional_shares == Decimal("0.4")

    def test_dividend_action_is_cash_only(self) -> None:
        adj = apply_action(
            _action(CorporateActionType.CASH_DIVIDEND, cash="0.24"),
            quantity=Decimal("41"),
            avg_cost=Decimal("123.7"),
        )
        assert adj.position_changed is False
        assert adj.quantity_after == Decimal("41")
        assert adj.avg_cost_after == Decimal("123.7")
        assert adj.cash_delta == Decimal("9.84")
        assert adj.realized_pnl == Decimal("9.84")

    def test_split_without_ratio_is_not_applicable_and_raises(self) -> None:
        action = _action(CorporateActionType.SPLIT_FORWARD)
        assert is_applicable(action) is False
        with pytest.raises(CorporateActionRuleError):
            apply_action(action, quantity=Decimal("1"), avg_cost=Decimal("1"))

    def test_dividend_without_amount_is_not_applicable(self) -> None:
        assert is_applicable(_action(CorporateActionType.CASH_DIVIDEND)) is False

    def test_ratio_of_one_is_rejected(self) -> None:
        with pytest.raises(CorporateActionRuleError, match="changes nothing"):
            apply_action(
                _action(CorporateActionType.SPLIT_FORWARD, ratio="1"),
                quantity=Decimal("1"),
                avg_cost=Decimal("1"),
            )

    @pytest.mark.parametrize(
        "action_type",
        [
            CorporateActionType.MERGER_STOCK,
            CorporateActionType.MERGER_CASH,
            CorporateActionType.SPINOFF,
            CorporateActionType.STOCK_DIVIDEND,
            CorporateActionType.NAME_CHANGE,
        ],
    )
    def test_other_types_are_manual(self, action_type: CorporateActionType) -> None:
        action = _action(action_type, cash="1")
        assert is_applicable(action) is False
        with pytest.raises(CorporateActionRuleError, match="not applied automatically"):
            apply_action(action, quantity=Decimal("1"), avg_cost=Decimal("1"))


class TestSplitFactor:
    def test_cumulative_factor_over_two_splits(self) -> None:
        actions = [
            _action(CorporateActionType.SPLIT_FORWARD, ratio="10", action_id="s1"),
            _action(CorporateActionType.SPLIT_FORWARD, ratio="4", action_id="s2"),
            _action(CorporateActionType.CASH_DIVIDEND, cash="0.1", action_id="d1"),
            _action(CorporateActionType.SPLIT_FORWARD, action_id="no-ratio"),
        ]
        assert split_factor_before(actions) == Decimal("0.025")

    def test_no_splits_is_one(self) -> None:
        assert split_factor_before([]) == Decimal("1")
