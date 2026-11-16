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
    # Market impact: impact_coefficient_bps * volume_share
    impact_coefficient_bps: Decimal = Decimal("25")
    max_volume_share: Decimal = Decimal("0.10")
    # Used when bar volume is unavailable (spread-aware fallback floor)
    fallback_min_bps: Decimal = Decimal("5")
    # Cost every fill pays whatever its size: half the bid-ask spread. The impact
    # term alone goes to zero for small orders (research orders are 0.01% to 0.12%
    # of a bar, so it charged 0.01 to 0.06 bps). 1 bp is the mean cost of 1,513
    # paper fills on Box A whose quote was tight, 2026-10-19 to 2026-11-13
    # (docs/roadmaps/research-generation-plan.md, R12). Set here, not at the
    # call sites, so every model built with defaults charges it.
    half_spread_bps: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.half_spread_bps < 0:
            raise ValueError(f"half_spread_bps must be >= 0, got {self.half_spread_bps}")


class VolumeShareSlippageModel:
    """Half-spread plus volume-participation slippage model.

    Primary path:
        slippage_bps = half_spread_bps
                       + impact_coefficient_bps * min(qty/bar_volume, max_volume_share)
    Fallback when bar volume is absent: half-spread proxy from OHLC range, clamped to
    fallback_min_bps. The fallback is already a spread estimate, so half_spread_bps is
    not added to it.
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
            return self.config.half_spread_bps + self.config.impact_coefficient_bps * capped_share

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
            # Part of the summary so cache identities change with it: results
            # simulated under a different spread must not be reused.
            "half_spread_bps": str(self.config.half_spread_bps),
        }

    @classmethod
    def from_calibration_snapshot(
        cls, snapshot: SlippageCalibrationSnapshot
    ) -> VolumeShareSlippageModel:
        """Construct a model with parameters derived from a calibration snapshot.

        Uses calibrated_impact_coefficient_bps and calibrated_fallback_min_bps from
        the snapshot.  Falls back gracefully if the snapshot is not globally calibrated
        (is_globally_calibrated=False) — in that case the snapshot already contains safe
        default values. The snapshot carries no spread term, so half_spread_bps keeps
        its default.
        """
        config = VolumeShareSlippageConfig(
            impact_coefficient_bps=snapshot.calibrated_impact_coefficient_bps,
            fallback_min_bps=snapshot.calibrated_fallback_min_bps,
            max_volume_share=snapshot.calibrated_max_volume_share,
        )
        return cls(config=config)
