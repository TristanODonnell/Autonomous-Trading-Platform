from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.safety.errors import (
    OrdersPerBarLimitExceededError,
    OrdersPerHourLimitExceededError,
    RepeatedOrderInBarError,
)
from autonomous_trading_platform.safety.services.order_throttle_service import (
    OrderThrottleService,
)
from tests.utilities.factories import make_order_intent


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
        side: Side,
        bar_timestamp: datetime,
    ) -> bool:
        return self._repeated_in_bar


class TimestampAwareOrderActivityReader:
    def __init__(
        self,
        *,
        hourly_count: int = 0,
        per_bar_counts: dict[datetime, int] | None = None,
        repeated_orders_by_bar: dict[tuple[str, Side, datetime], bool] | None = None,
    ) -> None:
        self._hourly_count = hourly_count
        self._per_bar_counts = per_bar_counts or {}
        self._repeated_orders_by_bar = repeated_orders_by_bar or {}

    def count_orders_between(self, start: datetime, end: datetime) -> int:
        return self._hourly_count

    def count_orders_for_bar(self, bar_timestamp: datetime) -> int:
        return self._per_bar_counts.get(bar_timestamp, 0)

    def has_matching_order_in_bar(
        self,
        *,
        symbol: str,
        side: Side,
        bar_timestamp: datetime,
    ) -> bool:
        return self._repeated_orders_by_bar.get((symbol, side, bar_timestamp), False)


def _settings(
    *,
    max_orders_per_hour: int = 5,
    max_orders_per_bar: int = 1,
    block_repeat_orders_same_bar: bool = True,
) -> Settings:
    return cast(
        Settings,
        cast(
            object,
            SimpleNamespace(
                max_orders_per_hour=max_orders_per_hour,
                max_orders_per_bar=max_orders_per_bar,
                block_repeat_orders_same_bar=block_repeat_orders_same_bar,
            ),
        ),
    )


def _order_intent(
    *,
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    idempotency_key: str = "abc-123",
    bar_timestamp: datetime | None = None,
):
    effective_bar_timestamp = bar_timestamp or datetime(2025, 1, 1, 15, 30, tzinfo=UTC)
    return make_order_intent(
        symbol=symbol,
        side=side,
        idempotency_key=idempotency_key,
        bar_timestamp=effective_bar_timestamp,
        timestamp=effective_bar_timestamp,
    )


def test_order_throttle_allows_order_when_within_limits() -> None:
    bar_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

    service = OrderThrottleService(
        settings=_settings(),
        order_activity_reader=FakeOrderActivityReader(),
    )

    service.assert_order_allowed_for_submission(
        order_intent=_order_intent(bar_timestamp=bar_timestamp),
        now=bar_timestamp,
        bar_timestamp=bar_timestamp,
    )


def test_order_throttle_blocks_when_hourly_limit_reached() -> None:
    bar_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

    service = OrderThrottleService(
        settings=_settings(max_orders_per_hour=5),
        order_activity_reader=FakeOrderActivityReader(orders_this_hour=5),
    )

    with pytest.raises(OrdersPerHourLimitExceededError, match="trailing hour"):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(bar_timestamp=bar_timestamp),
            now=bar_timestamp,
            bar_timestamp=bar_timestamp,
        )


def test_order_throttle_blocks_when_bar_limit_reached() -> None:
    bar_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

    service = OrderThrottleService(
        settings=_settings(max_orders_per_bar=1),
        order_activity_reader=FakeOrderActivityReader(orders_this_bar=1),
    )

    with pytest.raises(OrdersPerBarLimitExceededError, match="reached limit"):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(bar_timestamp=bar_timestamp),
            now=bar_timestamp,
            bar_timestamp=bar_timestamp,
        )


def test_order_throttle_blocks_repeated_order_in_same_bar_when_enabled() -> None:
    bar_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

    service = OrderThrottleService(
        settings=_settings(block_repeat_orders_same_bar=True, max_orders_per_bar=5),
        order_activity_reader=FakeOrderActivityReader(repeated_in_bar=True),
    )

    with pytest.raises(RepeatedOrderInBarError, match="Repeated order blocked"):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(
                symbol="AAPL",
                side=Side.BUY,
                bar_timestamp=bar_timestamp,
            ),
            now=bar_timestamp,
            bar_timestamp=bar_timestamp,
        )


def test_order_throttle_allows_repeated_order_when_same_bar_blocking_disabled() -> None:
    bar_timestamp = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

    service = OrderThrottleService(
        settings=_settings(block_repeat_orders_same_bar=False, max_orders_per_bar=5),
        order_activity_reader=FakeOrderActivityReader(repeated_in_bar=True),
    )

    service.assert_order_allowed_for_submission(
        order_intent=_order_intent(
            symbol="AAPL",
            side=Side.BUY,
            bar_timestamp=bar_timestamp,
        ),
        now=bar_timestamp,
        bar_timestamp=bar_timestamp,
    )


def test_order_throttle_resets_per_bar_limit_when_crossing_into_new_bar() -> None:
    first_bar = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)
    second_bar = first_bar + timedelta(minutes=5)

    reader = TimestampAwareOrderActivityReader(
        per_bar_counts={
            first_bar: 1,
            second_bar: 0,
        }
    )
    service = OrderThrottleService(
        settings=_settings(max_orders_per_bar=1),
        order_activity_reader=reader,
    )

    with pytest.raises(OrdersPerBarLimitExceededError):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(bar_timestamp=first_bar),
            now=first_bar,
            bar_timestamp=first_bar,
        )

    service.assert_order_allowed_for_submission(
        order_intent=_order_intent(bar_timestamp=second_bar),
        now=second_bar,
        bar_timestamp=second_bar,
    )


def test_order_throttle_repeat_prevention_is_scoped_to_current_bar() -> None:
    first_bar = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)
    second_bar = first_bar + timedelta(minutes=5)

    reader = TimestampAwareOrderActivityReader(
        repeated_orders_by_bar={
            ("AAPL", Side.BUY, first_bar): True,
            ("AAPL", Side.BUY, second_bar): False,
        }
    )
    service = OrderThrottleService(
        settings=_settings(block_repeat_orders_same_bar=True, max_orders_per_bar=5),
        order_activity_reader=reader,
    )

    with pytest.raises(RepeatedOrderInBarError):
        service.assert_order_allowed_for_submission(
            order_intent=_order_intent(
                symbol="AAPL",
                side=Side.BUY,
                bar_timestamp=first_bar,
            ),
            now=first_bar,
            bar_timestamp=first_bar,
        )

    service.assert_order_allowed_for_submission(
        order_intent=_order_intent(
            symbol="AAPL",
            side=Side.BUY,
            bar_timestamp=second_bar,
        ),
        now=second_bar,
        bar_timestamp=second_bar,
    )


def test_order_throttle_prevents_race_condition_for_shared_concurrent_submissions() -> None:

    shared_bar = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)

    reader = TimestampAwareOrderActivityReader(
        hourly_count=3,
        per_bar_counts={shared_bar: 0},
    )
    service = OrderThrottleService(
        settings=_settings(max_orders_per_hour=5, max_orders_per_bar=1),
        order_activity_reader=reader,
    )

    strategy_alpha_order = _order_intent(
        symbol="AAPL",
        side=Side.BUY,
        idempotency_key="alpha-order",
        bar_timestamp=shared_bar,
    )
    strategy_beta_order = _order_intent(
        symbol="AAPL",
        side=Side.BUY,
        idempotency_key="beta-order",
        bar_timestamp=shared_bar,
    )

    service.assert_order_allowed_for_submission(
        order_intent=strategy_alpha_order,
        now=shared_bar,
        bar_timestamp=shared_bar,
    )

    with pytest.raises(
        OrdersPerBarLimitExceededError,
        match="reached limit",
    ):
        service.assert_order_allowed_for_submission(
            order_intent=strategy_beta_order,
            now=shared_bar,
            bar_timestamp=shared_bar,
        )
