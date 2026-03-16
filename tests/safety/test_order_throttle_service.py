from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.safety.errors import (
    OrdersPerBarLimitExceededError,
    OrdersPerHourLimitExceededError,
    RepeatedOrderInBarError,
)
from autonomous_trading_platform.safety.services.order_throttle_service import (
    OrderThrottleService,
)


class FakeOrderActivityReader:
    def __init__(
        self,
        *,
        idempotency_exists: bool = False,
        orders_this_hour: int = 0,
        orders_this_bar: int = 0,
        repeated_in_bar: bool = False,
    ) -> None:
        self._idempotency_exists = idempotency_exists
        self._orders_this_hour = orders_this_hour
        self._orders_this_bar = orders_this_bar
        self._repeated_in_bar = repeated_in_bar

    def idempotency_key_exists(self, idempotency_key: str) -> bool:
        return self._idempotency_exists

    def count_orders_between(self, start: datetime, end: datetime) -> int:
        return self._orders_this_hour

    def count_orders_for_bar(self, bar_timestamp: datetime) -> int:
        return self._orders_this_bar

    def has_matching_order_in_bar(
        self,
        *,
        symbol: str,
        side,
        bar_timestamp: datetime,
    ) -> bool:
        return self._repeated_in_bar


def _settings(*, block_repeat_orders_same_bar: bool = True) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            max_orders_per_hour=5,
            max_orders_per_bar=1,
            block_repeat_orders_same_bar=block_repeat_orders_same_bar,
        ),
    )


def _order_intent(
    *,
    symbol: str = "AAPL",
    side: str = "buy",
    idempotency_key: str = "abc-123",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        side=side,
        idempotency_key=idempotency_key,
    )


def test_order_throttle_allows_order_when_within_limits() -> None:
    service = OrderThrottleService(
        settings=_settings(),
        order_activity_reader=FakeOrderActivityReader(),
    )

    service.assert_order_allowed_for_submission(
        order_intent=_order_intent(),
        now=datetime.now(UTC),
        bar_timestamp=datetime.now(UTC),
    )


def test_order_throttle_blocks_when_hourly_limit_reached() -> None:
    service = OrderThrottleService(
        settings=_settings(),
        order_activity_reader=FakeOrderActivityReader(orders_this_hour=5),
    )

    with pytest.raises(OrdersPerHourLimitExceededError, match="trailing hour"):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(),
            now=datetime.now(UTC),
            bar_timestamp=datetime.now(UTC),
        )


def test_order_throttle_blocks_when_bar_limit_reached() -> None:
    service = OrderThrottleService(
        settings=_settings(),
        order_activity_reader=FakeOrderActivityReader(orders_this_bar=1),
    )

    with pytest.raises(OrdersPerBarLimitExceededError, match="reached limit"):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(),
            now=datetime.now(UTC),
            bar_timestamp=datetime.now(UTC),
        )


def test_order_throttle_blocks_repeated_order_in_same_bar_when_enabled() -> None:
    service = OrderThrottleService(
        settings=_settings(block_repeat_orders_same_bar=True),
        order_activity_reader=FakeOrderActivityReader(repeated_in_bar=True),
    )

    with pytest.raises(RepeatedOrderInBarError, match="Repeated order blocked"):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(symbol="AAPL", side="buy"),
            now=datetime.now(UTC),
            bar_timestamp=datetime.now(UTC),
        )


def test_order_throttle_allows_repeated_order_when_same_bar_blocking_disabled() -> None:
    service = OrderThrottleService(
        settings=_settings(block_repeat_orders_same_bar=False),
        order_activity_reader=FakeOrderActivityReader(repeated_in_bar=True),
    )

    service.assert_order_allowed_for_submission(
        order_intent=_order_intent(symbol="AAPL", side="buy"),
        now=datetime.now(UTC),
        bar_timestamp=datetime.now(UTC),
    )
