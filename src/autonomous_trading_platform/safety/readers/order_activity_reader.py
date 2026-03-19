from __future__ import annotations

from datetime import datetime


class StubOrderActivityReader:
    def idempotency_key_exists(self, idempotency_key: str) -> bool:
        return False

    def count_orders_submitted_since(self, since: datetime) -> int:
        return 0

    def count_symbol_orders_submitted_since(self, symbol: str, since: datetime) -> int:
        return 0
