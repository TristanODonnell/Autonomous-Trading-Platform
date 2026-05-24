from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.fill import Fill

ZERO = Decimal("0")


@dataclass
class CashLedgerResult:
    cash: Decimal
    buying_power: Decimal
    reserved_cash: Decimal
    total_costs: Decimal


class CashLedgerService:
    def apply_fill(
        self,
        existing_snapshot: CashSnapshot | None,
        fill: Fill,
        commissions: Decimal = Decimal("0"),
        fees: Decimal = Decimal("0"),
    ) -> CashLedgerResult:
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

        starting_cash = Decimal(existing_snapshot.cash) if existing_snapshot is not None else ZERO
        starting_reserved_cash = (
            Decimal(existing_snapshot.reserved_cash) if existing_snapshot is not None else ZERO
        )

        gross_notional = price * quantity
        total_costs = commissions + fees

        if fill.side == Side.BUY:
            cash_delta = -(gross_notional + total_costs)
            # BUY fills consume reservation: the order was reserved at scheduling time,
            # so release min(pool, fill_notional) from the aggregate reservation pool.
            released_reserved_cash = min(starting_reserved_cash, gross_notional)
            reserved_cash = starting_reserved_cash - released_reserved_cash
        elif fill.side == Side.SELL:
            cash_delta = gross_notional - total_costs
            # SELL fills never consume reservation — reserved_cash tracks pending BUY
            # obligations and is unaffected by proceeds from selling existing positions.
            reserved_cash = starting_reserved_cash
        else:
            raise ValueError(f"unsupported fill side: {fill.side}")

        cash = starting_cash + cash_delta
        # Buying power is cash net of cash already committed to pending buy orders.
        buying_power = cash - reserved_cash

        return CashLedgerResult(
            cash=cash,
            buying_power=buying_power,
            reserved_cash=reserved_cash,
            total_costs=total_costs,
        )

    def reserve_order(
        self,
        existing_snapshot: CashSnapshot | None,
        notional: Decimal,
    ) -> CashLedgerResult:
        """Reserve cash for a pending buy order.

        Raises ValueError if notional exceeds available buying power.
        """
        if notional < ZERO:
            raise ValueError("notional cannot be negative")

        starting_cash = Decimal(existing_snapshot.cash) if existing_snapshot is not None else ZERO
        starting_reserved = (
            Decimal(existing_snapshot.reserved_cash) if existing_snapshot is not None else ZERO
        )

        free_buying_power = starting_cash - starting_reserved
        if notional > free_buying_power:
            raise ValueError(
                f"cannot reserve {notional}: buying power {free_buying_power} is insufficient"
            )

        new_reserved = starting_reserved + notional
        return CashLedgerResult(
            cash=starting_cash,
            buying_power=starting_cash - new_reserved,
            reserved_cash=new_reserved,
            total_costs=ZERO,
        )

    def release_reservation(
        self,
        existing_snapshot: CashSnapshot | None,
        notional: Decimal,
    ) -> CashLedgerResult:
        """Release reserved cash for a canceled, rejected, or expired order."""
        if notional < ZERO:
            raise ValueError("notional cannot be negative")

        starting_cash = Decimal(existing_snapshot.cash) if existing_snapshot is not None else ZERO
        starting_reserved = (
            Decimal(existing_snapshot.reserved_cash) if existing_snapshot is not None else ZERO
        )

        released = min(starting_reserved, notional)
        new_reserved = starting_reserved - released
        return CashLedgerResult(
            cash=starting_cash,
            buying_power=starting_cash - new_reserved,
            reserved_cash=new_reserved,
            total_costs=ZERO,
        )
