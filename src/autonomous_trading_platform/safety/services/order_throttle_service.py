from datetime import datetime, timedelta

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.safety.errors import (
    OrdersPerBarLimitExceededError,
    OrdersPerHourLimitExceededError,
    RepeatedOrderInBarError,
)


class OrderThrottleService:
    def __init__(self, settings: Settings, order_activity_reader) -> None:
        self.settings = settings
        self.order_activity_reader = order_activity_reader

    def assert_order_allowed_for_submission(
        self,
        order_intent,
        now: datetime,
        bar_timestamp: datetime,
    ) -> None:
        self._assert_hourly_order_limit(now)
        self._assert_bar_order_limit(bar_timestamp)

        if self.settings.block_repeat_orders_same_bar:
            self._assert_not_repeated_within_bar(order_intent, bar_timestamp)

    def _assert_hourly_order_limit(self, now: datetime) -> None:
        hour_start = now - timedelta(hours=1)
        orders_this_hour = self.order_activity_reader.count_orders_between(hour_start, now)

        if orders_this_hour >= self.settings.max_orders_per_hour:
            raise OrdersPerHourLimitExceededError(
                f"Orders in trailing hour {orders_this_hour} reached limit "
                f"{self.settings.max_orders_per_hour}."
            )

    def _assert_bar_order_limit(self, bar_timestamp: datetime) -> None:
        orders_this_bar = self.order_activity_reader.count_orders_for_bar(bar_timestamp)

        if orders_this_bar >= self.settings.max_orders_per_bar:
            raise OrdersPerBarLimitExceededError(
                f"Orders for bar {bar_timestamp.isoformat()} reached limit "
                f"{self.settings.max_orders_per_bar}."
            )

    def _assert_not_repeated_within_bar(self, order_intent, bar_timestamp: datetime) -> None:
        repeated = self.order_activity_reader.has_matching_order_in_bar(
            symbol=order_intent.symbol,
            side=order_intent.side,
            bar_timestamp=bar_timestamp,
        )

        if repeated:
            raise RepeatedOrderInBarError(
                f"Repeated order blocked for {order_intent.symbol} in "
                f"bar {bar_timestamp.isoformat()}."
            )
