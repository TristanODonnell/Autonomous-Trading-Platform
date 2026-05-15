# autonomous_trading_platform/contracts/validators/universe_version.py

from __future__ import annotations

import re

from autonomous_trading_platform.contracts.common.enums import UniverseStatus
from autonomous_trading_platform.contracts.runtime.universe_version import (
    UniverseMember,
    UniverseVersion,
)

from .core import Rule

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9._-]*$")

_VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    UniverseStatus.CANDIDATE: {UniverseStatus.PROPOSED, UniverseStatus.ACTIVE},
    UniverseStatus.PROPOSED: {UniverseStatus.ACTIVE, UniverseStatus.RETIRED},
    UniverseStatus.ACTIVE: {UniverseStatus.RETIRED},
    UniverseStatus.RETIRED: set(),
}

VALID_STATUSES: frozenset[str] = frozenset(s.value for s in UniverseStatus)


UNIVERSE_VERSION_RULES: list[Rule[UniverseVersion]] = [
    Rule(
        code="STATUS_VALID",
        field="status",
        check=lambda uv, _ctx: uv.status in VALID_STATUSES,
        message=lambda uv, _ctx: f"status '{uv.status}' is not a valid UniverseStatus",
    ),
    Rule(
        code="EFFECTIVE_WINDOW_ORDER_VALID",
        field=None,
        check=lambda uv, _ctx: uv.effective_to is None or uv.effective_from <= uv.effective_to,
        message=lambda uv, _ctx: (
            "effective_from must be <= effective_to when effective_to is present"
        ),
    ),
    Rule(
        code="CONFIG_HASH_NON_EMPTY",
        field="config_hash",
        check=lambda uv, _ctx: bool(uv.config_hash),
        message=lambda uv, _ctx: "config_hash must be non-empty",
    ),
    Rule(
        code="NAME_NON_EMPTY",
        field="name",
        check=lambda uv, _ctx: bool(uv.name and uv.name.strip()),
        message=lambda uv, _ctx: "name must be non-empty",
    ),
    Rule(
        code="SOURCE_NON_EMPTY",
        field="source",
        check=lambda uv, _ctx: bool(uv.source and uv.source.strip()),
        message=lambda uv, _ctx: "source must be non-empty",
    ),
]

UNIVERSE_MEMBER_RULES: list[Rule[UniverseMember]] = [
    Rule(
        code="SYMBOL_FORMAT_VALID",
        field="symbol",
        check=lambda m, _ctx: (
            isinstance(m.symbol, str) and _SYMBOL_RE.fullmatch(m.symbol) is not None
        ),
        message=lambda m, _ctx: f"symbol '{m.symbol}' does not match valid ticker format",
    ),
    Rule(
        code="RANK_NON_NEGATIVE",
        field="rank",
        check=lambda m, _ctx: m.rank is None or m.rank >= 0,
        message=lambda m, _ctx: "rank must be >= 0 when present",
    ),
]


def is_valid_transition(from_status: str, to_status: str) -> bool:
    allowed = _VALID_STATUS_TRANSITIONS.get(from_status, set())
    return to_status in allowed
