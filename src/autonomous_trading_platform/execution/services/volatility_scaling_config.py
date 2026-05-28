# autonomous_trading_platform/execution/services/volatility_scaling_config.py

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class VolatilityScalingConfig:
    """
    Controls how PortfolioConstructionService enforces volatility-aware sizing.

    volatility_scaling_enabled:
        Master switch.  When False no scalar is computed and full notional is
        used.  Correct for strategies that explicitly opt out of vol targeting.
        Preserves pre-FINDING-18 behaviour when set to False.

    require_vol_scalar_for_live:
        When True and trading_environment is LIVE, a missing combined_scalar
        raises MissingPositionScalingDataError before any order is sized.
        Prevents the most dangerous silent fallback: a full-size live order
        placed during a high-vol regime because bar data was temporarily
        unavailable.

    allow_full_size_when_scalars_missing:
        Permits full-size orders when scalars are unavailable.  Primarily for
        paper trading where a warn-and-continue posture is acceptable.  Has
        no effect in live mode when require_vol_scalar_for_live is True.

    min_position_scaling_notional_for_audit:
        Base-notional floor for emitting POSITION_SCALING_ABSENT.  Avoids
        metric noise from negligibly small positions.
    """

    volatility_scaling_enabled: bool = True
    require_vol_scalar_for_live: bool = True
    allow_full_size_when_scalars_missing: bool = False
    min_position_scaling_notional_for_audit: Decimal = Decimal("100.00")
