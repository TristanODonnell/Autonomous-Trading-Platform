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
        elif fill.side == Side.SELL:
            cash_delta = gross_notional - total_costs
        else:
            raise ValueError(f"unsupported fill side: {fill.side}")

        cash = starting_cash + cash_delta
        released_reserved_cash = min(starting_reserved_cash, gross_notional)
        reserved_cash = starting_reserved_cash - released_reserved_cash
        # v1 simplification:
        # buying power just mirrors available cash.
        buying_power = cash

        return CashLedgerResult(
            cash=cash,
            buying_power=buying_power,
            reserved_cash=reserved_cash,
            total_costs=total_costs,
        )
