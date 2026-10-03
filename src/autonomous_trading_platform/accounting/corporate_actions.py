"""The one rule for applying a corporate action to a long position.

Used by the account book and cash ledger (backtests), the real and shadow strategy
sleeves, and the research simulator, so a split or dividend changes share counts and
cash the same way everywhere and never creates P&L (plan 5d, decision D3).

Split (forward or reverse, ``ratio`` = new shares per old share):
    quantity  → quantity × ratio, rounded down to whole shares
    avg_cost  → avg_cost ÷ ratio            (cost basis is preserved)
    remainder → paid as cash in lieu at ``cash_in_lieu_price`` (the ex-date price);
                the small difference between that cash and the remainder's cost
                basis is realized P&L, as a broker would book it

Cash dividend: cash += shares held at the ex-date open × rate. Settled immediately.

Only forward/reverse splits with a ratio and cash dividends with an amount are
applied; every other stored action type is for manual review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Protocol

from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.contracts.simulation.dividend_event import DividendEvent

ZERO = Decimal("0")
ONE = Decimal("1")

APPLIED_ACTION_TYPES: frozenset[CorporateActionType] = frozenset(
    {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
        CorporateActionType.CASH_DIVIDEND,
    }
)


class CorporateActionRuleError(ValueError):
    """The action cannot be applied as stored (missing ratio / amount, bad inputs)."""


@dataclass(frozen=True)
class SplitResult:
    """Outcome of applying a split to one long position."""

    quantity: Decimal
    avg_cost: Decimal
    # Whole-share change (quantity_after − quantity_before); negative for a reverse split.
    quantity_delta: Decimal
    # Fractional post-split shares that could not be held and were paid in cash.
    fractional_shares: Decimal
    cash_in_lieu: Decimal
    # cash_in_lieu − cost basis of the fractional shares (zero when none).
    realized_pnl: Decimal


@dataclass(frozen=True)
class PositionAdjustment:
    """What one action does to one position: the new holding and the cash it throws off."""

    action_id: str
    symbol: str
    action_type: CorporateActionType
    quantity_before: Decimal
    quantity_after: Decimal
    avg_cost_before: Decimal
    avg_cost_after: Decimal
    cash_delta: Decimal
    realized_pnl: Decimal
    # For splits: the fractional remainder paid in cash. Zero for dividends.
    fractional_shares: Decimal = ZERO

    @property
    def quantity_delta(self) -> Decimal:
        return self.quantity_after - self.quantity_before

    @property
    def position_changed(self) -> bool:
        return (
            self.quantity_after != self.quantity_before
            or self.avg_cost_after != self.avg_cost_before
        )


def is_applicable(action: CorporateAction) -> bool:
    """True when the platform applies this action to its books."""
    if action.action_type in {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
    }:
        return (
            action.split_ratio is not None
            and action.split_ratio > ZERO
            and Decimal(str(action.split_ratio)) != ONE
        )
    if action.action_type is CorporateActionType.CASH_DIVIDEND:
        return action.cash_amount is not None and action.cash_amount > ZERO
    return False


def split_ratio_of(action: CorporateAction) -> Decimal:
    if action.action_type not in {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
    }:
        raise CorporateActionRuleError(f"{action.action_id}: not a split ({action.action_type})")
    if action.split_ratio is None or action.split_ratio <= ZERO:
        raise CorporateActionRuleError(f"{action.action_id}: split has no usable ratio")
    ratio = Decimal(str(action.split_ratio))
    if ratio == ONE:
        raise CorporateActionRuleError(f"{action.action_id}: split ratio of 1 changes nothing")
    return ratio


def dividend_per_share_of(action: CorporateAction) -> Decimal:
    if action.action_type is not CorporateActionType.CASH_DIVIDEND:
        raise CorporateActionRuleError(
            f"{action.action_id}: not a cash dividend ({action.action_type})"
        )
    if action.cash_amount is None or action.cash_amount <= ZERO:
        raise CorporateActionRuleError(f"{action.action_id}: dividend has no usable amount")
    return Decimal(str(action.cash_amount))


def apply_split(
    quantity: Decimal,
    avg_cost: Decimal,
    ratio: Decimal,
    *,
    cash_in_lieu_price: Decimal | None = None,
) -> SplitResult:
    """Apply a split to a long position of ``quantity`` shares at ``avg_cost``.

    ``cash_in_lieu_price`` is the post-split price used to pay out a fractional
    remainder (the ex-date price). When it is unknown the remainder is paid at its
    cost basis, so nothing is gained or lost.
    """
    quantity = Decimal(quantity)
    avg_cost = Decimal(avg_cost)
    ratio = Decimal(ratio)
    if quantity < ZERO:
        raise CorporateActionRuleError("short positions are not supported")
    if avg_cost < ZERO:
        raise CorporateActionRuleError("avg_cost cannot be negative")
    if ratio <= ZERO:
        raise CorporateActionRuleError("split ratio must be positive")

    raw_quantity = quantity * ratio
    whole = raw_quantity.to_integral_value(rounding=ROUND_FLOOR)
    fractional = raw_quantity - whole
    new_avg_cost = avg_cost / ratio

    if fractional > ZERO:
        price = Decimal(cash_in_lieu_price) if cash_in_lieu_price is not None else new_avg_cost
        cash_in_lieu = fractional * price
        realized = cash_in_lieu - fractional * new_avg_cost
    else:
        cash_in_lieu = ZERO
        realized = ZERO

    return SplitResult(
        quantity=whole,
        avg_cost=new_avg_cost,
        quantity_delta=whole - quantity,
        fractional_shares=fractional,
        cash_in_lieu=cash_in_lieu,
        realized_pnl=realized,
    )


def dividend_cash(quantity: Decimal, cash_per_share: Decimal) -> Decimal:
    quantity = Decimal(quantity)
    if quantity < ZERO:
        raise CorporateActionRuleError("short positions are not supported")
    return quantity * Decimal(cash_per_share)


def apply_action(
    action: CorporateAction,
    *,
    quantity: Decimal,
    avg_cost: Decimal,
    cash_in_lieu_price: Decimal | None = None,
) -> PositionAdjustment:
    """Apply one action to one long position and describe the result.

    Raises ``CorporateActionRuleError`` for action types the platform does not apply
    (``is_applicable`` is False) or for malformed inputs.
    """
    quantity = Decimal(quantity)
    avg_cost = Decimal(avg_cost)

    if action.action_type in {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
    }:
        result = apply_split(
            quantity, avg_cost, split_ratio_of(action), cash_in_lieu_price=cash_in_lieu_price
        )
        return PositionAdjustment(
            action_id=action.action_id,
            symbol=action.symbol,
            action_type=action.action_type,
            quantity_before=quantity,
            quantity_after=result.quantity,
            avg_cost_before=avg_cost,
            avg_cost_after=result.avg_cost,
            cash_delta=result.cash_in_lieu,
            realized_pnl=result.realized_pnl,
            fractional_shares=result.fractional_shares,
        )

    if action.action_type is CorporateActionType.CASH_DIVIDEND:
        cash = dividend_cash(quantity, dividend_per_share_of(action))
        return PositionAdjustment(
            action_id=action.action_id,
            symbol=action.symbol,
            action_type=action.action_type,
            quantity_before=quantity,
            quantity_after=quantity,
            avg_cost_before=avg_cost,
            avg_cost_after=avg_cost,
            cash_delta=cash,
            # A dividend is income, not a change in the position's cost basis.
            realized_pnl=cash,
        )

    raise CorporateActionRuleError(
        f"{action.action_id}: {action.action_type.value} is not applied automatically"
    )


def split_factor_before(actions: list[CorporateAction]) -> Decimal:
    """Cumulative price factor for bars before every split in ``actions``.

    Multiplying a pre-split price by this factor expresses it in post-split terms
    (1/ratio per split). Non-split actions are ignored.
    """
    factor = ONE
    for action in actions:
        if action.action_type in {
            CorporateActionType.SPLIT_FORWARD,
            CorporateActionType.SPLIT_REVERSE,
        } and is_applicable(action):
            factor /= split_ratio_of(action)
    return factor


# ---------------------------------------------------------------------------
# Split-adjusted history (plan 5d, decision D5: adjust on read, splits only)
# ---------------------------------------------------------------------------


class SplitSource(Protocol):
    """Where a context builder gets the splits for a symbol."""

    def splits_for(self, symbol: str, start_date: date, end_date: date) -> list[CorporateAction]:
        """Splits on ``symbol`` with an ex-date in ``[start_date, end_date]``."""
        ...


class StaticSplitSource:
    """Splits held in memory (research windows, tests)."""

    def __init__(
        self, actions: Mapping[str, Sequence[CorporateAction]] | Sequence[CorporateAction]
    ):
        by_symbol: dict[str, list[CorporateAction]] = {}
        items = (
            [a for actions_ in actions.values() for a in actions_]
            if isinstance(actions, Mapping)
            else list(actions)
        )
        for action in items:
            by_symbol.setdefault(action.symbol.upper(), []).append(action)
        self._by_symbol = by_symbol

    def splits_for(self, symbol: str, start_date: date, end_date: date) -> list[CorporateAction]:
        return [
            a
            for a in self._by_symbol.get(symbol.upper(), [])
            if is_split(a) and start_date <= a.effective_date <= end_date
        ]


def is_cash_dividend(action: CorporateAction) -> bool:
    return action.action_type is CorporateActionType.CASH_DIVIDEND


def dividend_events_from(actions: Sequence[CorporateAction]) -> list[DividendEvent]:
    """The research engine's dividend events for the applicable cash dividends in
    ``actions`` (one event per action, ex-date = effective date)."""
    return [
        DividendEvent(
            symbol=action.symbol,
            ex_date=action.effective_date,
            cash_amount_per_share=dividend_per_share_of(action),
            payable_date=action.payable_date,
            declaration_date=action.announced_date,
            currency=action.currency or "USD",
        )
        for action in actions
        if is_cash_dividend(action) and is_applicable(action)
    ]


class CorporateActionSource(Protocol):
    """Where research loads the applicable actions (splits and cash dividends) for a
    set of symbols over a window."""

    def actions_for(
        self, *, symbols: Sequence[str], start_date: date, end_date: date
    ) -> list[CorporateAction]: ...


def is_split(action: CorporateAction) -> bool:
    return action.action_type in {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
    } and is_applicable(action)


def split_factor_for(splits: Sequence[CorporateAction], *, bar_date: date, as_of: date) -> Decimal:
    """Price factor that expresses a bar dated ``bar_date`` in the share terms of
    ``as_of``: 1/ratio for every split with ``bar_date < ex_date <= as_of``.

    Splits after ``as_of`` are not applied — a decision made on ``as_of`` cannot know
    about them (no lookahead).
    """
    factor = ONE
    for split in splits:
        if not is_split(split):
            continue
        if bar_date < split.effective_date <= as_of:
            factor /= split_ratio_of(split)
    return factor


def adjust_bar(bar: MarketBar, factor: Decimal) -> MarketBar:
    """A copy of ``bar`` with prices × factor and volume ÷ factor (factor 1 → the bar)."""
    if factor == ONE:
        return bar
    if factor <= ZERO:
        raise CorporateActionRuleError("split adjustment factor must be positive")
    volume = int((Decimal(bar.volume) / factor).to_integral_value(rounding=ROUND_HALF_UP))
    adjusted: MarketBar = bar.model_copy(
        update={
            "open": bar.open * factor,
            "high": bar.high * factor,
            "low": bar.low * factor,
            "close": bar.close * factor,
            "vwap": bar.vwap * factor if bar.vwap is not None else None,
            "volume": volume,
            "price_basis": PriceBasis.ADJUSTED,
            "adjustment_factor": bar.adjustment_factor * factor,
        }
    )
    return adjusted


def adjust_bars_for_splits(
    bars: Sequence[MarketBar],
    splits: Sequence[CorporateAction],
    *,
    as_of: date,
) -> list[MarketBar]:
    """Express every bar before a split's ex-date in post-split terms, so a strategy
    reading history across the split sees a continuous series. Fills are never
    adjusted: they happen at the raw price of their day."""
    applicable = [s for s in splits if is_split(s) and s.effective_date <= as_of]
    if not applicable:
        return list(bars)
    out: list[MarketBar] = []
    for bar in bars:
        factor = split_factor_for(applicable, bar_date=bar.timestamp.date(), as_of=as_of)
        out.append(adjust_bar(bar, factor))
    return out
