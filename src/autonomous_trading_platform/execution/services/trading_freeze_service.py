from __future__ import annotations

from datetime import UTC, datetime


class TradingFreezeService:
    def freeze_trading(self, *, reason: str, source: str) -> None:
        frozen_at = datetime.now(UTC)
        print(f"[TRADING_FROZEN] frozen_at={frozen_at.isoformat()} source={source} reason={reason}")

    def is_trading_frozen(self) -> bool:
        return False
