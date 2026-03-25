from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.safety.errors import DuplicateIdempotencyKeyError
from autonomous_trading_platform.safety.services.order_idempotency_service import (
    OrderIdempotencyService,
)
from tests.utilities.factories import make_order_intent


class SettingsStub:
    def __init__(
        self,
        *,
        idempotency_deduplication_window_minutes: int = 15,
    ) -> None:
        self.idempotency_deduplication_window_minutes = idempotency_deduplication_window_minutes


def make_settings(dedupe_window_minutes: int = 15) -> Settings:
    return cast(
        Settings,
        SettingsStub(idempotency_deduplication_window_minutes=dedupe_window_minutes),
    )


@pytest.fixture
def order_activity_reader() -> Mock:
    reader = Mock()
    reader.idempotency_key_exists_between.return_value = False
    return reader


@pytest.fixture
def audit_log_repository() -> Mock:
    return Mock()


@pytest.fixture
def service(
    order_activity_reader: Mock,
    audit_log_repository: Mock,
) -> OrderIdempotencyService:
    return OrderIdempotencyService(
        settings=make_settings(),
        order_activity_reader=order_activity_reader,
        audit_log_repository=audit_log_repository,
    )


class TestOrderIdempotencyServiceCurrentBehavior:
    def test_build_idempotency_key_is_deterministic_across_calls(
        self,
        service: OrderIdempotencyService,
    ) -> None:
        order_intent = make_order_intent()

        key_1 = service.build_idempotency_key(order_intent)
        key_2 = service.build_idempotency_key(order_intent)

        assert key_1 == key_2
        assert isinstance(key_1, str)
        assert len(key_1) == 64

    @pytest.mark.parametrize(
        ("field_name", "changed_order_intent"),
        [
            (
                "run_id",
                make_order_intent(run_id=UUID("00000000-0000-0000-0000-000000000299")),
            ),
            ("strategy_id", make_order_intent(strategy_id="strategy-beta")),
            (
                "bar_timestamp",
                make_order_intent(bar_timestamp=datetime(2025, 1, 1, 15, 35, tzinfo=UTC)),
            ),
            ("symbol", make_order_intent(symbol="MSFT")),
            ("side", make_order_intent(side=Side.SELL)),
            ("qty", make_order_intent(qty=Decimal("11"))),
        ],
    )
    def test_build_idempotency_key_is_sensitive_to_all_fields(
        self,
        service: OrderIdempotencyService,
        field_name: str,
        changed_order_intent,
    ) -> None:
        baseline = make_order_intent()

        baseline_key = service.build_idempotency_key(baseline)
        changed_key = service.build_idempotency_key(changed_order_intent)

        assert baseline_key != changed_key, f"Key should change when {field_name} changes"

    def test_build_idempotency_key_normalizes_symbol_case(
        self,
        service: OrderIdempotencyService,
    ) -> None:
        upper = make_order_intent(symbol="AAPL")
        lower = make_order_intent(symbol="aapl")

        assert service.build_idempotency_key(upper) == service.build_idempotency_key(lower)

    def test_build_idempotency_key_rejects_missing_qty(
        self,
        service: OrderIdempotencyService,
    ) -> None:
        order_intent = make_order_intent(qty=None)

        with pytest.raises(
            ValueError,
            match="Order intent qty must be set for idempotency key generation.",
        ):
            service.build_idempotency_key(order_intent)

    def test_assert_not_duplicate_within_window_returns_key_and_checks_expected_window(
        self,
        service: OrderIdempotencyService,
        order_activity_reader: Mock,
    ) -> None:
        order_intent = make_order_intent()
        now = datetime(2025, 1, 1, 16, 0, tzinfo=UTC)

        result = service.assert_not_duplicate_within_window(
            order_intent=order_intent,
            now=now,
        )

        expected_key = service.build_idempotency_key(order_intent)
        expected_window_start = now - timedelta(minutes=15)

        assert result == expected_key
        order_activity_reader.idempotency_key_exists_between.assert_called_once_with(
            idempotency_key=expected_key,
            start_time=expected_window_start,
            end_time=now,
        )

    def test_assert_not_duplicate_within_window_raises_for_duplicate_key(
        self,
        service: OrderIdempotencyService,
        order_activity_reader: Mock,
    ) -> None:
        order_activity_reader.idempotency_key_exists_between.return_value = True
        order_intent = make_order_intent()
        now = datetime(2025, 1, 1, 16, 0, tzinfo=UTC)

        with pytest.raises(
            DuplicateIdempotencyKeyError,
            match="Duplicate order intent detected within deduplication window",
        ):
            service.assert_not_duplicate_within_window(
                order_intent=order_intent,
                now=now,
            )

    def test_key_does_not_collide_when_qty_changes(
        self,
        service: OrderIdempotencyService,
    ) -> None:
        key_1 = service.build_idempotency_key(make_order_intent(qty=Decimal("10")))
        key_2 = service.build_idempotency_key(make_order_intent(qty=Decimal("20")))

        assert key_1 != key_2

    def test_key_does_not_collide_when_side_changes(
        self,
        service: OrderIdempotencyService,
    ) -> None:
        buy_key = service.build_idempotency_key(make_order_intent(side=Side.BUY))
        sell_key = service.build_idempotency_key(make_order_intent(side=Side.SELL))

        assert buy_key != sell_key

    def test_assert_not_duplicate_within_window_records_successful_key_check(
        self,
        service: OrderIdempotencyService,
        audit_log_repository: Mock,
    ) -> None:
        order_intent = make_order_intent()
        now = datetime(2025, 1, 1, 16, 0, tzinfo=UTC)

        result = service.assert_not_duplicate_within_window(
            order_intent=order_intent,
            now=now,
        )

        expected_key = service.build_idempotency_key(order_intent)
        expected_window_start = now - timedelta(minutes=15)

        assert result == expected_key
        audit_log_repository.add.assert_called_once()

        logged_event = audit_log_repository.add.call_args.args[0]
        assert logged_event.run_id == str(order_intent.run_id)
        assert logged_event.event_type == "ORDER_IDEMPOTENCY_CHECK_PASSED"
        assert logged_event.component == "safety.order_idempotency"
        assert logged_event.event_timestamp == now
        assert logged_event.metadata["idempotency_key"] == expected_key
        assert (
            logged_event.metadata["window_start"]
            == expected_window_start.isoformat().replace("+00:00", "Z")
            or logged_event.metadata["window_start"] == expected_window_start.isoformat()
        )

    def test_assert_not_duplicate_within_window_records_duplicate_detection_event(
        self,
        service: OrderIdempotencyService,
        order_activity_reader: Mock,
        audit_log_repository: Mock,
    ) -> None:
        order_activity_reader.idempotency_key_exists_between.return_value = True
        order_intent = make_order_intent()
        now = datetime(2025, 1, 1, 16, 0, tzinfo=UTC)

        with pytest.raises(DuplicateIdempotencyKeyError):
            service.assert_not_duplicate_within_window(
                order_intent=order_intent,
                now=now,
            )

        expected_key = service.build_idempotency_key(order_intent)

        audit_log_repository.add.assert_called_once()
        logged_event = audit_log_repository.add.call_args.args[0]
        assert logged_event.run_id == str(order_intent.run_id)
        assert logged_event.event_type == "ORDER_IDEMPOTENCY_DUPLICATE_DETECTED"
        assert logged_event.component == "safety.order_idempotency"
        assert logged_event.event_timestamp == now
        assert logged_event.metadata["idempotency_key"] == expected_key
