from __future__ import annotations

from datetime import datetime

from autonomous_trading_platform.contracts.common.enums import (
    Side,
)


class StubOrderActivityReader:
    def idempotency_key_exists(self, idempotency_key: str) -> bool:
        return False

    def idempotency_key_exists_between(
        self,
        idempotency_key: str,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        return False

    def count_orders_submitted_since(self, since: datetime) -> int:
        return 0

    def count_symbol_orders_submitted_since(self, symbol: str, since: datetime) -> int:
        return 0

    def count_orders_between(self, start_time: datetime, end_time: datetime) -> int:
        return 0

    def count_symbol_orders_between(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        return 0

    def count_orders_for_bar(self, bar_timestamp: datetime) -> int:
        return 0

    def count_symbol_orders_for_bar(self, symbol: str, bar_timestamp: datetime) -> int:
        return 0

    def has_matching_order_in_bar(
        self,
        *,
        symbol: str,
        side: Side,
        bar_timestamp: datetime,
    ) -> bool:
        return False
