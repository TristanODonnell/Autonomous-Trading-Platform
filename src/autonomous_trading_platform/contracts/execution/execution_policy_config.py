from __future__ import annotations

import enum
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, Field, model_validator


class PolicyMode(enum.StrEnum):
    """Top-level execution style for an order."""

    PASSTHROUGH = "passthrough"  # honour whatever OrderType the strategy set
    MARKET = "market"  # force market order regardless of strategy signal
    LIMIT = "limit"  # force limit order; requires limit_offset_bps
    TWAP = "twap"  # time-weighted average price slicing
    VWAP_LITE = "vwap_lite"  # volume-weighted average price (lite) slicing


class LimitOrderPolicy(BaseModel):
    """Controls how limit prices are set when policy_mode=LIMIT (TASK-200)."""

    # Offset from mid-price in basis points; positive = more aggressive (tighter spread).
    # BUY  limit = mid * (1 + offset_bps / 10000)   [paying slightly above mid]
    # SELL limit = mid * (1 - offset_bps / 10000)   [receiving slightly below mid]
    offset_bps: Decimal = Field(default=Decimal("5"), ge=Decimal("0"))

    # If True and limit is not hit within the cycle, cancel and re-emit as market.
    fallback_to_market: bool = False


class TWAPConfig(BaseModel):
    """Parameters for TWAP order slicing"""

    # Total execution window in minutes.
    window_minutes: int = Field(default=30, ge=1, le=480)

    # Number of equal child slices to split the order into.
    num_slices: int = Field(default=6, ge=2, le=100)

    # Child order type for each slice.
    slice_order_type: str = Field(default="market")  # "market" | "limit"

    # Only relevant when slice_order_type="limit": offset from mid in bps.
    slice_limit_offset_bps: Decimal = Field(default=Decimal("3"), ge=Decimal("0"))


class VWAPLiteConfig(BaseModel):
    """Parameters for VWAP-lite order slicing (TASK-202).

    'Lite' because we use a static volume profile (e.g. uniform or pre-loaded
    intraday profile) rather than live order-book volume — appropriate for the
    current architecture which doesn't have a live order book feed.
    """

    # Total execution window in minutes.
    window_minutes: int = Field(default=30, ge=1, le=480)

    # Number of slices; actual quantities will be weighted by volume profile.
    num_slices: int = Field(default=6, ge=2, le=100)

    # Volume profile type: "uniform" or "u_shaped" (higher at open/close).
    volume_profile: str = Field(default="uniform")  # "uniform" | "u_shaped"


class SlippageConfig(BaseModel):
    """Controls how expected slippage is modelled pre-execution .

    These values are used by SlippageCalculator to compute the expected
    SlippageMeasurement before submission.  Post-execution, actual slippage
    is computed from the Fill.
    """

    # Fixed slippage rate applied to reference price.
    # fill_price = reference_price * (1 + rate) for buys,
    # fill_price = reference_price * (1 - rate) for sells.
    fixed_slippage_rate: Decimal = Field(default=Decimal("0.0005"), ge=Decimal("0"))  # 5 bps

    # Whether to model spread cost (half-spread added to slippage).
    include_spread_cost: bool = True

    # Commission per share (for CostBreakdown).
    commission_per_share: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))


class ExecutionPolicyConfig(BaseModel):
    """
    Complete execution policy for one strategy/instrument combination.

    Attach to RunManifest or strategy config; embed in OrderIntent.metadata
    under the key "execution_policy" if per-order override is needed.

    Example (JSON):
        {
            "policy_mode": "twap",
            "twap": {"window_minutes": 20, "num_slices": 4},
            "slippage": {"fixed_slippage_rate": "0.0003"}
        }
    """

    policy_mode: PolicyMode = PolicyMode.PASSTHROUGH

    # Mode-specific sub-configs — only the relevant one is used.
    limit_policy: LimitOrderPolicy = Field(default_factory=LimitOrderPolicy)
    twap: TWAPConfig = Field(default_factory=TWAPConfig)
    vwap_lite: VWAPLiteConfig = Field(default_factory=VWAPLiteConfig)
    slippage: SlippageConfig = Field(default_factory=SlippageConfig)

    # Whether to emit fill quality analytics to Postgres .
    record_fill_quality: bool = True

    @model_validator(mode="after")
    def _validate_mode_configs(self) -> ExecutionPolicyConfig:
        if self.policy_mode == PolicyMode.LIMIT and self.limit_policy is None:
            raise ValueError("limit_policy is required when policy_mode=LIMIT")
        if self.policy_mode == PolicyMode.TWAP and self.twap is None:
            raise ValueError("twap config is required when policy_mode=TWAP")
        if self.policy_mode == PolicyMode.VWAP_LITE and self.vwap_lite is None:
            raise ValueError("vwap_lite config is required when policy_mode=VWAP_LITE")
        return self

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPolicyConfig:
        return cast(ExecutionPolicyConfig, cls.model_validate(data))

    @classmethod
    def passthrough(cls) -> ExecutionPolicyConfig:
        """Convenience factory: no-op policy, honours strategy's OrderType as-is."""
        return cls(policy_mode=PolicyMode.PASSTHROUGH)

    @classmethod
    def market_only(cls) -> ExecutionPolicyConfig:
        """Convenience factory: force all orders to market type."""
        return cls(policy_mode=PolicyMode.MARKET)
