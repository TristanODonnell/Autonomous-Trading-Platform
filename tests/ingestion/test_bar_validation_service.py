from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autonomous_trading_platform.ingestion.market_data.services.bar_validation_service import (
    BarValidationService,
)
from tests.utilities.factories import make_five_minute_bar


class TestBarValidationService:
    def test_is_late_bar_returns_false_exactly_at_threshold(self) -> None:
        service = BarValidationService()
        allowed_delay = timedelta(seconds=30)
        bar = make_five_minute_bar(timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC))

        now_utc = bar.end_timestamp + allowed_delay

        assert (
            service.is_late_bar(
                bar=bar,
                now_utc=now_utc,
                allowed_delay=allowed_delay,
            )
            is False
        )

    def test_is_late_bar_returns_false_just_inside_threshold(self) -> None:
        service = BarValidationService()
        allowed_delay = timedelta(seconds=30)
        bar = make_five_minute_bar(timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC))

        now_utc = bar.end_timestamp + allowed_delay - timedelta(microseconds=1)

        assert (
            service.is_late_bar(
                bar=bar,
                now_utc=now_utc,
                allowed_delay=allowed_delay,
            )
            is False
        )

    def test_is_late_bar_returns_true_just_outside_threshold(self) -> None:
        service = BarValidationService()
        allowed_delay = timedelta(seconds=30)
        bar = make_five_minute_bar(timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC))

        now_utc = bar.end_timestamp + allowed_delay + timedelta(microseconds=1)

        assert (
            service.is_late_bar(
                bar=bar,
                now_utc=now_utc,
                allowed_delay=allowed_delay,
            )
            is True
        )

    def test_is_suspected_outlier_returns_false_when_reference_close_is_none(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="120.00",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=None,
            )
            is False
        )

    def test_is_suspected_outlier_returns_false_when_reference_close_is_zero(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="120.00",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("0"),
            )
            is False
        )

    def test_is_suspected_outlier_returns_false_exactly_at_threshold(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="120.00",
        )

        # 20% move exactly, and implementation uses ">" not ">="
        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
            )
            is False
        )

    def test_is_suspected_outlier_returns_false_just_inside_threshold(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="119.99",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
            )
            is False
        )

    def test_is_suspected_outlier_returns_true_just_outside_threshold(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="120.01",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
            )
            is True
        )

    def test_is_suspected_outlier_detects_large_up_move(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="135.00",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
            )
            is True
        )

    def test_is_suspected_outlier_detects_large_down_move(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="75.00",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
            )
            is True
        )

    def test_is_suspected_outlier_allows_reasonable_small_move_to_reduce_false_positives(
        self,
    ) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="101.50",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.05"),
            )
            is False
        )

    def test_validate_bar_returns_validation_result_for_valid_bar(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC))

        result = service.validate_bar(bar)

        # Keep this intentionally lightweight since MARKET_BAR_RULES belong
        # to contract-validator coverage more than service logic coverage.
        assert result is not None

    def test_is_suspected_outlier_detects_volume_spike_based_on_multiplier(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            volume=1_000_000,
            close_price="100.50",
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
                reference_volume=50_000,
                max_volume_multiplier=Decimal("10"),
            )
            is True
        )

    def test_is_suspected_outlier_uses_threshold_selection_to_minimize_volume_false_positives(
        self,
    ) -> None:
        service = BarValidationService()
        moderate_volume_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            volume=25_000,
            close_price="100.20",
        )

        assert (
            service.is_suspected_outlier(
                bar=moderate_volume_bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
                reference_volume=10_000,
                max_volume_multiplier=Decimal("3"),
            )
            is False
        )

    def test_is_suspected_outlier_returns_false_when_reference_volume_is_none(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC), volume=500_000
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
                reference_volume=None,
                max_volume_multiplier=Decimal("10"),
            )
            is False
        )

    def test_is_suspected_outlier_returns_false_when_volume_is_exactly_at_multiplier_threshold(
        self,
    ) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC), volume=100_000
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=None,
                reference_volume=10_000,
                max_volume_multiplier=Decimal("10"),
            )
            is False
        )

    def test_is_suspected_outlier_returns_true_when_price_is_normal_but_volume_spikes(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC),
            close_price="100.10",
            volume=500_000,
        )

        assert (
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("0.20"),
                reference_volume=20_000,
                max_volume_multiplier=Decimal("10"),
            )
            is True
        )

    def test_is_suspected_outlier_raises_for_negative_price_threshold(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC))

        with pytest.raises(ValueError, match="max_move_pct must be non-negative"):
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                max_move_pct=Decimal("-0.01"),
            )

    def test_is_suspected_outlier_raises_for_non_positive_volume_multiplier(self) -> None:
        service = BarValidationService()
        bar = make_five_minute_bar(timestamp=datetime(2025, 1, 1, 14, 30, tzinfo=UTC))

        with pytest.raises(ValueError, match="max_volume_multiplier must be greater than zero"):
            service.is_suspected_outlier(
                bar=bar,
                reference_close=Decimal("100.00"),
                reference_volume=10_000,
                max_volume_multiplier=Decimal("0"),
            )
