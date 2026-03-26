from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    CorporateActionType,
    PriceBasis,
)
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_adjustment_service import (
    CorporateActionAdjustmentService,
)
from tests.utilities.factories import make_five_minute_bar


def make_action(
    *,
    action_type: CorporateActionType,
    symbol: str = "AAPL",
    effective_date: date = date(2025, 1, 15),
    split_ratio: str | None = None,
    cash_amount: str | None = None,
) -> CorporateAction:
    return CorporateAction(
        action_id="ca-123",
        symbol=symbol,
        action_type=action_type,
        effective_date=effective_date,
        announced_date=None,
        record_date=None,
        payable_date=None,
        split_ratio=Decimal(split_ratio) if split_ratio is not None else None,
        cash_amount=Decimal(cash_amount) if cash_amount is not None else None,
        currency="USD",
        new_symbol="",
        source="test",
        ingested_at=datetime.now(UTC),
        metadata={},
    )


class TestCorporateActionAdjustmentService:
    def test_supports_adjustment_returns_true_for_split_actions(self) -> None:
        service = CorporateActionAdjustmentService()

        forward_split = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            split_ratio="4",
        )
        reverse_split = make_action(
            action_type=CorporateActionType.SPLIT_REVERSE,
            split_ratio="0.25",
        )

        assert service.supports_adjustment(forward_split) is True
        assert service.supports_adjustment(reverse_split) is True

    def test_supports_adjustment_returns_false_for_non_split_actions(self) -> None:
        service = CorporateActionAdjustmentService()

        dividend = make_action(
            action_type=CorporateActionType.CASH_DIVIDEND,
            cash_amount="1.25",
        )

        assert service.supports_adjustment(dividend) is False

    def test_compute_adjustment_factor_for_forward_split(self) -> None:
        action = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            split_ratio="4",
        )

        factor = CorporateActionAdjustmentService._compute_adjustment_factor(action)

        assert factor == Decimal("0.25")

    def test_compute_adjustment_factor_for_reverse_split(self) -> None:
        action = make_action(
            action_type=CorporateActionType.SPLIT_REVERSE,
            split_ratio="0.2",
        )

        factor = CorporateActionAdjustmentService._compute_adjustment_factor(action)

        assert factor == Decimal("5")

    def test_compute_adjustment_factor_raises_when_split_ratio_missing(self) -> None:
        action = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            split_ratio=None,
        )

        with pytest.raises(ValueError, match="missing valid split_ratio"):
            CorporateActionAdjustmentService._compute_adjustment_factor(action)

    def test_compute_adjustment_factor_raises_when_split_ratio_zero(self) -> None:
        action = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            split_ratio="0",
        )

        with pytest.raises(ValueError, match="missing valid split_ratio"):
            CorporateActionAdjustmentService._compute_adjustment_factor(action)

    def test_compute_adjustment_factor_raises_for_unsupported_action_type(self) -> None:
        action = make_action(
            action_type=CorporateActionType.CASH_DIVIDEND,
            cash_amount="1.00",
        )

        with pytest.raises(ValueError, match="not supported"):
            CorporateActionAdjustmentService._compute_adjustment_factor(action)

    def test_apply_factor_adjusts_split_prices_vwap_and_adjustment_factor(self) -> None:
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 10, 14, 30, tzinfo=UTC),
            open_price="100.00",
            high_price="120.00",
            low_price="80.00",
            close_price="110.00",
            vwap="105.00",
            volume=1000,
            adjustment_factor="1",
            price_basis=PriceBasis.RAW,
        )

        adjusted = CorporateActionAdjustmentService._apply_factor(
            bar=bar,
            factor=Decimal("0.25"),
        )

        assert adjusted.open == Decimal("25.00")
        assert adjusted.high == Decimal("30.00")
        assert adjusted.low == Decimal("20.00")
        assert adjusted.close == Decimal("27.50")
        assert adjusted.vwap == Decimal("26.25")
        assert adjusted.adjustment_factor == Decimal("0.25")

        assert adjusted.symbol == bar.symbol
        assert adjusted.timestamp == bar.timestamp
        assert adjusted.end_timestamp == bar.end_timestamp
        assert adjusted.interval == bar.interval
        assert adjusted.price_basis == PriceBasis.ADJUSTED
        assert adjusted.bar_id != bar.bar_id

    def test_apply_factor_preserves_none_vwap(self) -> None:
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 10, 14, 30, tzinfo=UTC),
            vwap=None,
        )

        adjusted = CorporateActionAdjustmentService._apply_factor(
            bar=bar,
            factor=Decimal("0.5"),
        )

        assert adjusted.vwap is None

    def test_apply_action_to_bars_adjusts_only_bars_before_effective_date(self) -> None:
        service = CorporateActionAdjustmentService()
        action = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            split_ratio="4",
            effective_date=date(2025, 1, 15),
        )

        before_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 14, 14, 30, tzinfo=UTC),
            close_price="100.00",
            volume=1000,
        )
        on_date_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            close_price="100.00",
            volume=1000,
        )
        after_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 16, 14, 30, tzinfo=UTC),
            close_price="100.00",
            volume=1000,
        )

        adjusted_bars = service.apply_action_to_bars(
            action=action,
            bars=[before_bar, on_date_bar, after_bar],
        )

        assert len(adjusted_bars) == 3

        adjusted_before = adjusted_bars[0]
        unchanged_on_date = adjusted_bars[1]
        unchanged_after = adjusted_bars[2]

        assert adjusted_before.close == Decimal("25.00")
        assert adjusted_before.price_basis == PriceBasis.ADJUSTED
        assert adjusted_before.adjustment_factor == Decimal("0.25")

        assert unchanged_on_date.close == Decimal("100.00")
        assert unchanged_on_date.price_basis == before_bar.price_basis

        assert unchanged_after.close == Decimal("100.00")
        assert unchanged_after.price_basis == after_bar.price_basis

    def test_apply_action_to_bars_skips_bars_for_other_symbols(self) -> None:
        service = CorporateActionAdjustmentService()
        action = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            symbol="AAPL",
            split_ratio="2",
        )

        aapl_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 10, 14, 30, tzinfo=UTC),
            symbol="AAPL",
            close_price="100.00",
        )
        msft_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 10, 14, 35, tzinfo=UTC),
            symbol="MSFT",
            close_price="200.00",
        )

        adjusted_bars = service.apply_action_to_bars(
            action=action,
            bars=[aapl_bar, msft_bar],
        )

        assert len(adjusted_bars) == 1
        assert adjusted_bars[0].symbol == "AAPL"
        assert adjusted_bars[0].close == Decimal("50.00")

    def test_apply_factor_adjusts_volume_for_split(self) -> None:
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 10, 14, 30, tzinfo=UTC),
            volume=1000,
        )

        adjusted = CorporateActionAdjustmentService._apply_factor(
            bar=bar,
            factor=Decimal("0.25"),
        )

        # Example expected behavior for a 4-for-1 split:
        # prices × 0.25, volume × 4
        assert adjusted.volume == 4000

    def test_apply_action_to_bars_adjusts_prices_for_cash_dividend(self) -> None:
        service = CorporateActionAdjustmentService()
        action = make_action(
            action_type=CorporateActionType.CASH_DIVIDEND,
            cash_amount="1.50",
            effective_date=date(2025, 1, 15),
        )

        before_bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 14, 14, 30, tzinfo=UTC),
            open_price="100.00",
            high_price="101.00",
            low_price="99.00",
            close_price="100.50",
            vwap="100.25",
        )

        adjusted_bars = service.apply_action_to_bars(
            action=action,
            bars=[before_bar],
        )

        adjusted = adjusted_bars[0]
        assert adjusted.open == Decimal("98.50")
        assert adjusted.high == Decimal("99.50")
        assert adjusted.low == Decimal("97.50")
        assert adjusted.close == Decimal("99.00")
        assert adjusted.vwap == Decimal("98.75")

    @pytest.mark.xfail(
        reason=(
            "TODO: add integration coverage that persists adjusted bars through "
            "the actual ParquetDataset or SOR repository path and verifies stored values."
        ),
        strict=False,
    )
    def test_apply_action_to_bars_persists_adjusted_values_via_dataset_or_sor(self) -> None:
        service = CorporateActionAdjustmentService()
        action = make_action(
            action_type=CorporateActionType.SPLIT_FORWARD,
            split_ratio="2",
            effective_date=date(2025, 1, 15),
        )
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 1, 14, 14, 30, tzinfo=UTC),
            close_price="100.00",
        )

        adjusted_bars = service.apply_action_to_bars(action=action, bars=[bar])

        # TODO:
        # 1. persist adjusted_bars with real Parquet dataset writer or SOR repo
        # 2. read them back
        # 3. assert stored close == Decimal("50.00")
        # 4. assert stored price_basis == PriceBasis.ADJUSTED
        persisted_close = adjusted_bars[0].close
        assert persisted_close == Decimal("50.00")
