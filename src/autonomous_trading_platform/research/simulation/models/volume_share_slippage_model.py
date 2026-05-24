from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.cost_model_type import CostModelType
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext

if TYPE_CHECKING:
    from autonomous_trading_platform.research.calibration.models.calibration_snapshot import (
        SlippageCalibrationSnapshot,
    )

_BPS = Decimal("10000")
_HALF = Decimal("2")


@dataclass(slots=True)
class VolumeShareSlippageConfig:
    # Market impact: slippage_bps = impact_coefficient_bps * volume_share
    impact_coefficient_bps: Decimal = Decimal("25")
    max_volume_share: Decimal = Decimal("0.10")
    # Used when bar volume is unavailable (spread-aware fallback floor)
    fallback_min_bps: Decimal = Decimal("5")


class VolumeShareSlippageModel:
    """Volume-participation slippage model.

    Primary path: slippage_bps = impact_coefficient_bps * min(qty/bar_volume, max_volume_share).
    Fallback when bar volume is absent: half-spread proxy from OHLC range, clamped to
    fallback_min_bps.
    """

    model_type = CostModelType.VOLUME_SHARE

    def __init__(self, config: VolumeShareSlippageConfig | None = None) -> None:
        self.config = config if config is not None else VolumeShareSlippageConfig()

    def calculate_fill_price(
        self,
        *,
        side: Side,
        market_price: Decimal,
        context: SlippageContext | None = None,
    ) -> Decimal:
        slippage_bps = self._compute_slippage_bps(context)
        slippage_rate = slippage_bps / _BPS

        if side == Side.BUY:
            return market_price * (Decimal("1") + slippage_rate)
        if side == Side.SELL:
            return market_price * (Decimal("1") - slippage_rate)
        raise ValueError(f"unsupported side={side}")

    def _compute_slippage_bps(self, context: SlippageContext | None) -> Decimal:
        if context is not None and context.bar_volume is not None and context.bar_volume > 0:
            raw_share = context.quantity / context.bar_volume
            capped_share = min(raw_share, self.config.max_volume_share)
            return self.config.impact_coefficient_bps * capped_share

        # Spread-aware fallback: use OHLC range as half-spread proxy
        if (
            context is not None
            and context.bar_high is not None
            and context.bar_low is not None
            and context.bar_close is not None
            and context.bar_close > 0
        ):
            half_spread_bps = (
                (context.bar_high - context.bar_low) / context.bar_close / _HALF * _BPS
            )
            return max(self.config.fallback_min_bps, half_spread_bps)

        return self.config.fallback_min_bps

    def config_summary(self) -> dict[str, object]:
        return {
            "model_type": self.model_type.value,
            "impact_coefficient_bps": str(self.config.impact_coefficient_bps),
            "max_volume_share": str(self.config.max_volume_share),
            "fallback_min_bps": str(self.config.fallback_min_bps),
        }

    @classmethod
    def from_calibration_snapshot(
        cls, snapshot: SlippageCalibrationSnapshot
    ) -> VolumeShareSlippageModel:
        """Construct a model with parameters derived from a calibration snapshot.

        Uses calibrated_impact_coefficient_bps and calibrated_fallback_min_bps from
        the snapshot.  Falls back gracefully if the snapshot is not globally calibrated
        (is_globally_calibrated=False) — in that case the snapshot already contains safe
        default values.
        """
        config = VolumeShareSlippageConfig(
            impact_coefficient_bps=snapshot.calibrated_impact_coefficient_bps,
            fallback_min_bps=snapshot.calibrated_fallback_min_bps,
            max_volume_share=snapshot.calibrated_max_volume_share,
        )
        return cls(config=config)
