# autonomous_trading_platform/contracts/validators/position_snapshot.py

from __future__ import annotations

from autonomous_trading_platform.contracts.accounting.position_snapshot import PositionSnapshot

from .core import Rule, is_non_negative

POSITION_SNAPSHOT_RULES: list[Rule[PositionSnapshot]] = [
    Rule(
        code="UNIQUE_SYMBOLS_IN_POSITION_SNAPSHOT",
        field="positions",
        check=lambda ps, _ctx: len({p.symbol for p in ps.positions}) == len(ps.positions),
        message=lambda ps, _ctx: "positions must contain unique symbols",
    ),
    Rule(
        code="LONG_ONLY_QUANTITY_NON_NEGATIVE",
        field="positions",
        check=lambda ps, _ctx: all(is_non_negative(p.quantity) for p in ps.positions),
        message=lambda ps, _ctx: "all position quantities must be >= 0 in long-only mode",
    ),
]
