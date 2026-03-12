from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.market.market_bar import MarketBar


class CorporateActionAdjustmentService:
    def apply_action_to_bars(
        self,
        action: CorporateAction,
        bars: list[MarketBar],
    ) -> list[MarketBar]:
        factor = self._compute_adjustment_factor(action)

        adjusted_bars: list[MarketBar] = []
        for bar in bars:
            if bar.symbol != action.symbol:
                continue

            if bar.timestamp.date() >= action.effective_date:
                adjusted_bars.append(bar)
                continue

            adjusted_bars.append(self._apply_factor(bar, factor))

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

    @staticmethod
    def _apply_factor(bar: MarketBar, factor: Decimal) -> MarketBar:
        return MarketBar(
            bar_id=bar.bar_id,
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            end_timestamp=bar.end_timestamp,
            interval=bar.interval,
            open=bar.open * factor,
            high=bar.high * factor,
            low=bar.low * factor,
            close=bar.close * factor,
            volume=bar.volume,
            vwap=bar.vwap * factor if bar.vwap is not None else None,
            price_basis=PriceBasis.ADJUSTED,
            adjustment_factor=bar.adjustment_factor * factor,
            source=bar.source,
            ingested_at=bar.ingested_at,
            quality_flags=bar.quality_flags,
            session=bar.session,
        )
