from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.safety.errors import (
    OrdersPerBarLimitExceededError,
    OrdersPerHourLimitExceededError,
    RepeatedOrderInBarError,
)


@dataclass(frozen=True)
class _RepeatedOrderReservationKey:
    symbol: str
    side: object
    bar_timestamp: datetime


class OrderThrottleService:
    def __init__(self, settings: Settings, order_activity_reader) -> None:
        self.settings = settings
        self.order_activity_reader = order_activity_reader
        self._lock = Lock()
        self._reserved_orders_by_bar: defaultdict[datetime, int] = defaultdict(int)
        self._reserved_orders_by_timestamp: defaultdict[datetime, int] = defaultdict(int)
        self._reserved_repeat_keys: set[_RepeatedOrderReservationKey] = set()

    def assert_order_allowed_for_submission(
        self,
        order_intent,
        now: datetime,
        bar_timestamp: datetime,
    ) -> None:
        with self._lock:
            self._assert_hourly_order_limit(now)
            self._assert_bar_order_limit(bar_timestamp)

            if self.settings.block_repeat_orders_same_bar:
                self._assert_not_repeated_within_bar(order_intent, bar_timestamp)

            self._reserve_submission(
                order_intent=order_intent,
                now=now,
                bar_timestamp=bar_timestamp,
            )

    def _assert_hourly_order_limit(self, now: datetime) -> None:
        hour_start = now - timedelta(hours=1)
        orders_this_hour = self.order_activity_reader.count_orders_between(hour_start, now)
        reserved_this_hour = self._count_reserved_orders_between(hour_start, now)
        effective_orders_this_hour = orders_this_hour + reserved_this_hour

        if effective_orders_this_hour >= self.settings.max_orders_per_hour:
            raise OrdersPerHourLimitExceededError(
                f"Orders in trailing hour {effective_orders_this_hour} reached limit "
                f"{self.settings.max_orders_per_hour}."
            )

    def _assert_bar_order_limit(self, bar_timestamp: datetime) -> None:
        orders_this_bar = self.order_activity_reader.count_orders_for_bar(bar_timestamp)
        reserved_this_bar = self._reserved_orders_by_bar.get(bar_timestamp, 0)
        effective_orders_this_bar = orders_this_bar + reserved_this_bar

        if effective_orders_this_bar >= self.settings.max_orders_per_bar:
            raise OrdersPerBarLimitExceededError(
                f"Orders for bar {bar_timestamp.isoformat()} reached limit "
                f"{self.settings.max_orders_per_bar}."
            )

    def _assert_not_repeated_within_bar(self, order_intent, bar_timestamp: datetime) -> None:
        repeat_key = _RepeatedOrderReservationKey(
            symbol=order_intent.symbol,
            side=order_intent.side,
            bar_timestamp=bar_timestamp,
        )

        repeated_in_persisted_state = self.order_activity_reader.has_matching_order_in_bar(
            symbol=order_intent.symbol,
            side=order_intent.side,
            bar_timestamp=bar_timestamp,
        )
        repeated_in_reserved_state = repeat_key in self._reserved_repeat_keys

        if repeated_in_persisted_state or repeated_in_reserved_state:
            raise RepeatedOrderInBarError(
                f"Repeated order blocked for {order_intent.symbol} in "
                f"bar {bar_timestamp.isoformat()}."
            )

    def _reserve_submission(
        self,
        *,
        order_intent,
        now: datetime,
        bar_timestamp: datetime,
    ) -> None:
        self._prune_expired_hour_reservations(now)

        self._reserved_orders_by_bar[bar_timestamp] += 1
        self._reserved_orders_by_timestamp[now] += 1

        if self.settings.block_repeat_orders_same_bar:
            self._reserved_repeat_keys.add(
                _RepeatedOrderReservationKey(
                    symbol=order_intent.symbol,
                    side=order_intent.side,
                    bar_timestamp=bar_timestamp,
                )
            )

    def _count_reserved_orders_between(self, start: datetime, end: datetime) -> int:
        self._prune_expired_hour_reservations(end)

        total = 0
        for timestamp, count in self._reserved_orders_by_timestamp.items():
            if start <= timestamp <= end:
                total += count
        return total

    def _prune_expired_hour_reservations(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        expired_timestamps = [
            timestamp for timestamp in self._reserved_orders_by_timestamp if timestamp < cutoff
        ]
        for timestamp in expired_timestamps:
            del self._reserved_orders_by_timestamp[timestamp]
