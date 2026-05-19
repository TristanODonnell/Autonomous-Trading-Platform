from __future__ import annotations

from dataclasses import dataclass, field

from autonomous_trading_platform.contracts.runtime.raw_market_symbol import (
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)


@dataclass
class RawMarketSymbolValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.ok = False


def validate_raw_market_symbol(symbol: RawMarketSymbol) -> RawMarketSymbolValidationResult:
    result = RawMarketSymbolValidationResult()

    if not symbol.symbol or not symbol.symbol.strip():
        result.add_error("symbol must be non-empty")
    elif symbol.symbol != symbol.symbol.strip().upper():
        result.add_error(
            f"symbol must be normalized (uppercase, no whitespace): got '{symbol.symbol}'"
        )

    if not symbol.asset_type:
        result.add_error("asset_type must be non-empty")

    if not symbol.status:
        result.add_error("status must be non-empty")

    if not symbol.source:
        result.add_error("source must be non-empty")

    if symbol.first_seen > symbol.last_seen:
        result.add_error(
            f"first_seen ({symbol.first_seen.isoformat()}) must be <= "
            f"last_seen ({symbol.last_seen.isoformat()})"
        )

    return result


def validate_raw_market_pool_snapshot(
    snapshot: RawMarketPoolSnapshot,
) -> RawMarketSymbolValidationResult:
    result = RawMarketSymbolValidationResult()

    if not snapshot.snapshot_id:
        result.add_error("snapshot_id must be non-empty")

    if not snapshot.source:
        result.add_error("source must be non-empty")

    if snapshot.symbol_count < 0:
        result.add_error("symbol_count must be >= 0")

    if not snapshot.cadence:
        result.add_error("cadence must be non-empty")

    return result
