from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.contracts.validators.core import ValidationResult, run_rules
from autonomous_trading_platform.contracts.validators.market_bar import MARKET_BAR_RULES


@dataclass(frozen=True, slots=True)
class OutlierEvaluation:
    is_outlier: bool
    price_outlier: bool
    volume_outlier: bool
    move_pct: Decimal | None
    volume_ratio: Decimal | None


class BarValidationService:
    def validate_bar(self, bar: MarketBar) -> ValidationResult:
        return run_rules(bar, MARKET_BAR_RULES)

    def is_late_bar(
        self,
        bar: MarketBar,
        now_utc: datetime,
        allowed_delay: timedelta,
    ) -> bool:
        return now_utc > (bar.end_timestamp + allowed_delay)

    def is_suspected_outlier(
        self,
        bar: MarketBar,
        reference_close: Decimal | None,
        max_move_pct: Decimal = Decimal("0.20"),
        *,
        reference_volume: int | None = None,
        max_volume_multiplier: Decimal = Decimal("10"),
    ) -> bool:
        result = self.evaluate_outlier(
            bar=bar,
            reference_close=reference_close,
            max_move_pct=max_move_pct,
            reference_volume=reference_volume,
            max_volume_multiplier=max_volume_multiplier,
        )
        return result.is_outlier

    def evaluate_outlier(
        self,
        bar: MarketBar,
        reference_close: Decimal | None,
        max_move_pct: Decimal = Decimal("0.20"),
        *,
        reference_volume: int | None = None,
        max_volume_multiplier: Decimal = Decimal("10"),
    ) -> OutlierEvaluation:
        self._validate_thresholds(
            max_move_pct=max_move_pct,
            max_volume_multiplier=max_volume_multiplier,
        )

        price_outlier = False
        volume_outlier = False
        move_pct: Decimal | None = None
        volume_ratio: Decimal | None = None

        if reference_close is not None and reference_close > 0:
            move_pct = abs((bar.close - reference_close) / reference_close)
            price_outlier = move_pct > max_move_pct

        if reference_volume is not None and reference_volume > 0:
            volume_ratio = Decimal(bar.volume) / Decimal(reference_volume)
            volume_outlier = volume_ratio > max_volume_multiplier

        return OutlierEvaluation(
            is_outlier=price_outlier or volume_outlier,
            price_outlier=price_outlier,
            volume_outlier=volume_outlier,
            move_pct=move_pct,
            volume_ratio=volume_ratio,
        )

    @staticmethod
    def _validate_thresholds(
        *,
        max_move_pct: Decimal,
        max_volume_multiplier: Decimal,
    ) -> None:
        if max_move_pct < 0:
            raise ValueError("max_move_pct must be non-negative")
        if max_volume_multiplier <= 0:
            raise ValueError("max_volume_multiplier must be greater than zero")
