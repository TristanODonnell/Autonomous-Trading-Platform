# autonomous_trading_platform/contracts/validators/cash_snapshot.py

from __future__ import annotations

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot

from .core import Rule, is_non_negative

CASH_SNAPSHOT_RULES: list[Rule[CashSnapshot]] = [
    # cash >= 0
    Rule(
        code="CASH_NONNEG",
        field="cash",
        check=lambda cash, _ctx: is_non_negative(CashSnapshot.cash),
        message=lambda cash, _ctx: "cash value must be >= 0",
    ),
    # buying_power >= 0
    Rule(
        code="BUYING_POWER_NONNEG",
        field="buying_power",
        check=lambda buying_power, _ctx: is_non_negative(CashSnapshot.buying_power),
        message=lambda buying_power, _ctx: "buying_power value must be >= 0",
    ),
    # reserved_cash >= 0
    Rule(
        code="RESERVED_CASH_NONNEG",
        field="reserved_cash",
        check=lambda reserved_cash, _ctx: is_non_negative(CashSnapshot.reserved_cash),
        message=lambda reserved_cash, _ctx: "reserved_cash value must be >= 0",
    ),
    # reserved_cash <= cash + buying_power
    Rule(
        code="RESERVED_CASH_LESSTHANOREQUAL_CASH_PLUS_BUYING_POWER",
        field="reserved_cash",
        check=lambda reserved_cash, _ctx: (
            CashSnapshot.reserved_cash <= CashSnapshot.cash + CashSnapshot.buying_power
        ),
        message=lambda reserved_cash, _ctx: "reserved_cash value must be >= cash + buying_power",
    ),
]
