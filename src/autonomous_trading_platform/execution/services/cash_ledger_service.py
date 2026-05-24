from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.fill import Fill

ZERO = Decimal("0")


@dataclass
class CashLedgerResult:
    cash: Decimal  # total economic cash = settled + unsettled
    buying_power: Decimal  # settled_cash - reserved_cash
    reserved_cash: Decimal
    total_costs: Decimal
    settled_cash: Decimal = field(default_factory=lambda: Decimal("0"))
    unsettled_cash: Decimal = field(default_factory=lambda: Decimal("0"))


def _starting_settled(snapshot: CashSnapshot | None) -> Decimal:
    """Return settled cash from snapshot, defaulting to full cash for legacy snapshots."""
    if snapshot is None:
        return ZERO
    if snapshot.settled_cash is not None:
        return Decimal(snapshot.settled_cash)
    return Decimal(snapshot.cash)


def _starting_unsettled(snapshot: CashSnapshot | None) -> Decimal:
    """Return unsettled cash from snapshot, defaulting to zero for legacy snapshots."""
    if snapshot is None:
        return ZERO
    if snapshot.unsettled_cash is not None:
        return Decimal(snapshot.unsettled_cash)
    return ZERO


class CashLedgerService:
    def apply_fill(
        self,
        existing_snapshot: CashSnapshot | None,
        fill: Fill,
        commissions: Decimal = Decimal("0"),
        fees: Decimal = Decimal("0"),
        settlement_days: int = 0,
    ) -> CashLedgerResult:
        """Apply a fill to the cash ledger.

        With settlement_days=0 (default) SELL proceeds are immediately settled —
        preserving legacy behavior.  With settlement_days>0, SELL proceeds move to
        unsettled_cash until matured by the simulation engine; buying_power reflects
        only settled_cash minus reserved_cash.
        """
        quantity = Decimal(fill.quantity)
        price = Decimal(fill.price)

        if quantity <= ZERO:
            raise ValueError("fill.quantity must be positive")
        if price <= ZERO:
            raise ValueError("fill.price must be positive")
        if commissions < ZERO:
            raise ValueError("commissions cannot be negative")
        if fees < ZERO:
            raise ValueError("fees cannot be negative")
        if settlement_days < 0:
            raise ValueError("settlement_days cannot be negative")

        starting_reserved = (
            Decimal(existing_snapshot.reserved_cash) if existing_snapshot is not None else ZERO
        )
        starting_settled = _starting_settled(existing_snapshot)
        starting_unsettled = _starting_unsettled(existing_snapshot)

        gross_notional = price * quantity
        total_costs = commissions + fees

        if fill.side == Side.BUY:
            net_cost = gross_notional + total_costs
            # BUY fills consume reservation: release min(pool, fill_notional) from
            # the aggregate reservation pool.
            released_reserved = min(starting_reserved, gross_notional)
            reserved_cash = starting_reserved - released_reserved
            # BUY deducts from settled cash (reservations were against settled cash).
            settled_cash = starting_settled - net_cost
            unsettled_cash = starting_unsettled
        elif fill.side == Side.SELL:
            net_proceeds = gross_notional - total_costs
            # SELL fills never consume reservation — reserved_cash tracks pending BUY
            # obligations and is unaffected by proceeds from selling existing positions.
            reserved_cash = starting_reserved
            if settlement_days > 0:
                # Proceeds enter the unsettled bucket; buying power unchanged until mature.
                settled_cash = starting_settled
                unsettled_cash = starting_unsettled + net_proceeds
            else:
                # Immediate settlement (default / legacy behavior).
                settled_cash = starting_settled + net_proceeds
                unsettled_cash = starting_unsettled
        else:
            raise ValueError(f"unsupported fill side: {fill.side}")

        cash = settled_cash + unsettled_cash
        buying_power = settled_cash - reserved_cash

        return CashLedgerResult(
            cash=cash,
            buying_power=buying_power,
            reserved_cash=reserved_cash,
            total_costs=total_costs,
            settled_cash=settled_cash,
            unsettled_cash=unsettled_cash,
        )

    def reserve_order(
        self,
        existing_snapshot: CashSnapshot | None,
        notional: Decimal,
    ) -> CashLedgerResult:
        """Reserve cash for a pending buy order.

        Buying power is settled_cash - reserved_cash; unsettled proceeds cannot be
        reserved.  Raises ValueError if notional exceeds available buying power.
        """
        if notional < ZERO:
            raise ValueError("notional cannot be negative")

        starting_settled = _starting_settled(existing_snapshot)
        starting_unsettled = _starting_unsettled(existing_snapshot)
        starting_reserved = (
            Decimal(existing_snapshot.reserved_cash) if existing_snapshot is not None else ZERO
        )

        free_buying_power = starting_settled - starting_reserved
        if notional > free_buying_power:
            raise ValueError(
                f"cannot reserve {notional}: buying power {free_buying_power} is insufficient"
            )

        new_reserved = starting_reserved + notional
        cash = starting_settled + starting_unsettled
        return CashLedgerResult(
            cash=cash,
            buying_power=starting_settled - new_reserved,
            reserved_cash=new_reserved,
            total_costs=ZERO,
            settled_cash=starting_settled,
            unsettled_cash=starting_unsettled,
        )

    def release_reservation(
        self,
        existing_snapshot: CashSnapshot | None,
        notional: Decimal,
    ) -> CashLedgerResult:
        """Release reserved cash for a canceled, rejected, or expired order."""
        if notional < ZERO:
            raise ValueError("notional cannot be negative")

        starting_settled = _starting_settled(existing_snapshot)
        starting_unsettled = _starting_unsettled(existing_snapshot)
        starting_reserved = (
            Decimal(existing_snapshot.reserved_cash) if existing_snapshot is not None else ZERO
        )

        released = min(starting_reserved, notional)
        new_reserved = starting_reserved - released
        cash = starting_settled + starting_unsettled
        return CashLedgerResult(
            cash=cash,
            buying_power=starting_settled - new_reserved,
            reserved_cash=new_reserved,
            total_costs=ZERO,
            settled_cash=starting_settled,
            unsettled_cash=starting_unsettled,
        )
