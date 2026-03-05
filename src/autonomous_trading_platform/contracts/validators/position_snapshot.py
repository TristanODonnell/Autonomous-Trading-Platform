# autonomous_trading_platform/contracts/validators/position_snapshot.py

from __future__ import annotations

from autonomous_trading_platform.contracts.accounting.position_snapshot import PositionSnapshot

from .core import Rule, is_non_negative

POSITION_SNAPSHOT_RULES: list[Rule[PositionSnapshot]] = [
    # If v1 long-only: quantity >= 0 for all symbols.
    Rule(
        code="QUANTITY_NONNEG_FORALL_SYMBOLS",
        field="quantity",
        check=lambda ps, _ctx: all(is_non_negative(p.quantity) for p in ps.positions),
        message=lambda ps, _ctx: "Not all quantities are non-negative.",
    )
]
