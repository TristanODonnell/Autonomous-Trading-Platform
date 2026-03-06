# autonomous_trading_platform/contracts/validators/universe_snapshot.py

from __future__ import annotations

import re

from autonomous_trading_platform.contracts.runtime.universe_snapshot import UniverseSnapshot

from .core import Rule

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9._-]*$")


UNIVERSE_SNAPSHOT_RULES: list[Rule[UniverseSnapshot]] = [
    Rule(
        code="SYMBOLS_NON_EMPTY",
        field="symbols",
        check=lambda us, _ctx: len(us.symbols) > 0,
        message=lambda us, _ctx: "symbols must be non-empty",
    ),
    Rule(
        code="SYMBOLS_UNIQUE",
        field="symbols",
        check=lambda us, _ctx: len(set(us.symbols)) == len(us.symbols),
        message=lambda us, _ctx: "symbols must not contain duplicates",
    ),
    Rule(
        code="EFFECTIVE_WINDOW_ORDER_VALID",
        field=None,
        check=lambda us, _ctx: us.effective_end is None or us.effective_start <= us.effective_end,
        message=lambda us, _ctx: (
            "effective_start must be <= effective_end when effective_end is present"
        ),
    ),
    Rule(
        code="SYMBOL_FORMAT_VALID",
        field="symbols",
        check=lambda us, _ctx: all(
            isinstance(symbol, str) and _SYMBOL_RE.fullmatch(symbol) is not None
            for symbol in us.symbols
        ),
        message=lambda us, _ctx: "all symbols must have valid ticker format",
    ),
]
