from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.ingestion.helpers.bar_identity import build_bar_id


class CorporateActionAdjustmentService:
    def apply_action_to_bars(
        self,
        action: CorporateAction,
        bars: list[MarketBar],
    ) -> list[MarketBar]:
        adjusted_bars: list[MarketBar] = []

        for bar in bars:
            if bar.symbol != action.symbol:
                continue

            if bar.timestamp.date() >= action.effective_date:
                adjusted_bars.append(bar)
                continue

            if action.action_type in {
                CorporateActionType.SPLIT_FORWARD,
                CorporateActionType.SPLIT_REVERSE,
            }:
                factor = self._compute_adjustment_factor(action)
                adjusted_bars.append(self._apply_split_factor(bar, factor))
                continue

            if action.action_type == CorporateActionType.CASH_DIVIDEND:
                adjusted_bars.append(self._apply_cash_dividend(bar, action))
                continue

            raise ValueError(
                f"Adjustment factor not supported for action type: {action.action_type}"
            )

        return adjusted_bars

    @staticmethod
    def _compute_adjustment_factor(action: CorporateAction) -> Decimal:
        if action.action_type in {
            CorporateActionType.SPLIT_FORWARD,
            CorporateActionType.SPLIT_REVERSE,
        }:
            if action.split_ratio is None or action.split_ratio == 0:
                raise ValueError("Split action missing valid split_ratio")

            return Decimal("1") / Decimal(str(action.split_ratio))

        raise ValueError(f"Adjustment factor not supported for action type: {action.action_type}")

    @classmethod
    def _apply_split_factor(cls, bar: MarketBar, factor: Decimal) -> MarketBar:
        adjusted_bar_id = build_bar_id(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            interval=bar.interval,
            price_basis=PriceBasis.ADJUSTED,
        )

        adjusted_volume = cls._adjust_split_volume(
            volume=bar.volume,
            factor=factor,
        )

        return MarketBar(
            bar_id=adjusted_bar_id,
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            end_timestamp=bar.end_timestamp,
            interval=bar.interval,
            open=bar.open * factor,
            high=bar.high * factor,
            low=bar.low * factor,
            close=bar.close * factor,
            volume=adjusted_volume,
            vwap=bar.vwap * factor if bar.vwap is not None else None,
            trade_count=bar.trade_count,
            price_basis=PriceBasis.ADJUSTED,
            adjustment_factor=bar.adjustment_factor * factor,
            source=bar.source,
            ingested_at=bar.ingested_at,
            quality_flags=bar.quality_flags,
            session=bar.session,
        )

    @staticmethod
    def _adjust_split_volume(
        *,
        volume: int,
        factor: Decimal,
    ) -> int:
        if volume < 0:
            raise ValueError("Bar volume cannot be negative")
        if factor <= 0:
            raise ValueError("Split adjustment factor must be positive")

        adjusted = Decimal(volume) / factor
        return int(adjusted.to_integral_value(rounding=ROUND_HALF_UP))

    @staticmethod
    def _apply_cash_dividend(
        bar: MarketBar,
        action: CorporateAction,
    ) -> MarketBar:
        if action.cash_amount is None or action.cash_amount < 0:
            raise ValueError("Cash dividend action missing valid cash_amount")

        adjusted_bar_id = build_bar_id(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            interval=bar.interval,
            price_basis=PriceBasis.ADJUSTED,
        )

        cash_amount = Decimal(str(action.cash_amount))

        return MarketBar(
            bar_id=adjusted_bar_id,
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            end_timestamp=bar.end_timestamp,
            interval=bar.interval,
            open=bar.open - cash_amount,
            high=bar.high - cash_amount,
            low=bar.low - cash_amount,
            close=bar.close - cash_amount,
            volume=bar.volume,
            vwap=bar.vwap - cash_amount if bar.vwap is not None else None,
            trade_count=bar.trade_count,
            price_basis=PriceBasis.ADJUSTED,
            adjustment_factor=bar.adjustment_factor,
            source=bar.source,
            ingested_at=bar.ingested_at,
            quality_flags=bar.quality_flags,
            session=bar.session,
        )

    # Backwards-compatible name if tests or callers still reference _apply_factor
    @classmethod
    def _apply_factor(cls, bar: MarketBar, factor: Decimal) -> MarketBar:
        return cls._apply_split_factor(bar, factor)

    def supports_adjustment(self, action: CorporateAction) -> bool:
        return action.action_type in {
            CorporateActionType.SPLIT_FORWARD,
            CorporateActionType.SPLIT_REVERSE,
        }
