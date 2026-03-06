# autonomous_trading_platform/contracts/validators/risk_snapshot.py

from __future__ import annotations

from autonomous_trading_platform.contracts.accounting.risk_snapshot import RiskSnapshot

from .core import Rule, is_finite, is_non_negative

RISK_SNAPSHOT_RULES: list[Rule[RiskSnapshot]] = [
    Rule(
        code="NET_EXPOSURE_FINITE",
        field="net_exposure",
        check=lambda rs, _ctx: is_finite(rs.net_exposure),
        message=lambda rs, _ctx: "net_exposure must be finite",
    ),
    Rule(
        code="GROSS_EXPOSURE_FINITE",
        field="gross_exposure",
        check=lambda rs, _ctx: is_finite(rs.gross_exposure),
        message=lambda rs, _ctx: "gross_exposure must be finite",
    ),
    Rule(
        code="LEVERAGE_FINITE",
        field="leverage",
        check=lambda rs, _ctx: is_finite(rs.leverage),
        message=lambda rs, _ctx: "leverage must be finite",
    ),
    Rule(
        code="GROSS_EXPOSURE_GTE_ABS_NET_EXPOSURE",
        field=None,
        check=lambda rs, _ctx: rs.gross_exposure >= abs(rs.net_exposure),
        message=lambda rs, _ctx: (
            "gross_exposure must be >= abs(net_exposure) "
            f"(gross_exposure={rs.gross_exposure}, net_exposure={rs.net_exposure})"
        ),
    ),
    Rule(
        code="BLOCK_REASONS_REQUIRED_WHEN_BLOCKED",
        field="block_reasons",
        check=lambda rs, _ctx: (
            (not rs.is_blocked) or (rs.block_reasons is not None and len(rs.block_reasons) > 0)
        ),
        message=lambda rs, _ctx: "block_reasons must be non-empty when is_blocked=true",
    ),
    Rule(
        code="LEVERAGE_NON_NEGATIVE",
        field="leverage",
        check=lambda rs, _ctx: is_non_negative(rs.leverage),
        message=lambda rs, _ctx: "leverage must be >= 0",
    ),
]
