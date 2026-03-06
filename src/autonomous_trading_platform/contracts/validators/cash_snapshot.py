# autonomous_trading_platform/contracts/validators/cash_snapshot.py

from __future__ import annotations

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot

from .core import Rule, is_non_negative

CASH_SNAPSHOT_RULES: list[Rule[CashSnapshot]] = [
    Rule(
        code="CASH_NON_NEGATIVE",
        field="cash",
        check=lambda cash, _ctx: is_non_negative(cash.cash),
        message=lambda cash, _ctx: "cash must be >= 0",
    ),
    Rule(
        code="BUYING_POWER_NON_NEGATIVE",
        field="buying_power",
        check=lambda cash, _ctx: is_non_negative(cash.buying_power),
        message=lambda cash, _ctx: "buying_power must be >= 0",
    ),
    Rule(
        code="RESERVED_CASH_NON_NEGATIVE",
        field="reserved_cash",
        check=lambda cash, _ctx: is_non_negative(cash.reserved_cash),
        message=lambda cash, _ctx: "reserved_cash must be >= 0",
    ),
    Rule(
        code="RESERVED_CASH_LTE_CASH_PLUS_BUYING_POWER",
        field=None,
        check=lambda cash, _ctx: cash.reserved_cash <= (cash.cash + cash.buying_power),
        message=lambda cash, _ctx: (
            "reserved_cash must be <= cash + buying_power "
            f"(reserved_cash={cash.reserved_cash}, cash={cash.cash}, buying_power={cash.buying_power})"
        ),
    ),
]
