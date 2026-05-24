"""Tests for VolumeShareSlippageModel."""

from __future__ import annotations

from decimal import Decimal

import pytest

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.cost_model_type import CostModelType
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext
from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
    VolumeShareSlippageConfig,
    VolumeShareSlippageModel,
)


def _ctx(
    *,
    qty: str,
    volume: str | None = None,
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
) -> SlippageContext:
    return SlippageContext(
        quantity=Decimal(qty),
        bar_volume=Decimal(volume) if volume is not None else None,
        bar_high=Decimal(high) if high is not None else None,
        bar_low=Decimal(low) if low is not None else None,
        bar_close=Decimal(close) if close is not None else None,
    )


class TestVolumeShareSlippageModel:
    def test_model_type_is_volume_share(self):
        model = VolumeShareSlippageModel()
        assert model.model_type == CostModelType.VOLUME_SHARE

    def test_buy_fill_price_above_market(self):
        model = VolumeShareSlippageModel()
        ctx = _ctx(qty="100", volume="10000")
        price = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        assert price > Decimal("100")

    def test_sell_fill_price_below_market(self):
        model = VolumeShareSlippageModel()
        ctx = _ctx(qty="100", volume="10000")
        price = model.calculate_fill_price(side=Side.SELL, market_price=Decimal("100"), context=ctx)
        assert price < Decimal("100")

    def test_larger_order_relative_to_volume_produces_higher_slippage(self):
        """Higher volume share → higher slippage."""
        model = VolumeShareSlippageModel()
        market_price = Decimal("100")
        small_ctx = _ctx(qty="100", volume="10000")  # 1% volume share
        large_ctx = _ctx(qty="1000", volume="10000")  # 10% volume share

        small_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=small_ctx
        )
        large_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=large_ctx
        )

        assert large_fill > small_fill

    def test_same_order_higher_volume_produces_lower_slippage(self):
        """Lower volume share (more liquid bar) → lower slippage."""
        model = VolumeShareSlippageModel()
        market_price = Decimal("100")
        thin_ctx = _ctx(qty="100", volume="200")  # 50% capped to max_volume_share
        liquid_ctx = _ctx(qty="100", volume="100000")  # 0.1% volume share

        thin_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=thin_ctx
        )
        liquid_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=liquid_ctx
        )

        assert thin_fill > liquid_fill

    def test_volume_share_capped_at_max(self):
        """Volume share above max_volume_share is clamped."""
        config = VolumeShareSlippageConfig(
            impact_coefficient_bps=Decimal("25"),
            max_volume_share=Decimal("0.10"),
        )
        model = VolumeShareSlippageModel(config=config)
        market_price = Decimal("100")

        # 50% raw share — capped to 10%
        capped_ctx = _ctx(qty="500", volume="1000")
        # exactly 10% share
        exact_ctx = _ctx(qty="100", volume="1000")

        capped_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=capped_ctx
        )
        exact_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=exact_ctx
        )

        assert capped_fill == exact_fill

    def test_volume_share_formula(self):
        """slippage_bps = impact_coefficient_bps * volume_share."""
        config = VolumeShareSlippageConfig(
            impact_coefficient_bps=Decimal("50"),
            max_volume_share=Decimal("1.0"),
        )
        model = VolumeShareSlippageModel(config=config)
        # qty=100, volume=1000 → volume_share=0.1 → slippage_bps=5 → rate=0.0005
        ctx = _ctx(qty="100", volume="1000")
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        expected = Decimal("100") * (Decimal("1") + Decimal("5") / Decimal("10000"))
        assert fill == expected

    def test_fallback_to_spread_when_no_volume(self):
        """When bar_volume is absent, uses OHLC spread as proxy."""
        model = VolumeShareSlippageModel()
        ctx = _ctx(qty="100", high="102", low="98", close="100")  # no volume
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        # spread = (102-98)/100/2*10000 = 200 bps → well above 1 bps
        assert fill > Decimal("100") * (Decimal("1") + Decimal("0.0001"))

    def test_fallback_uses_min_bps_when_no_context(self):
        """No context at all → uses fallback_min_bps."""
        config = VolumeShareSlippageConfig(fallback_min_bps=Decimal("10"))
        model = VolumeShareSlippageModel(config=config)
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=None)
        expected = Decimal("100") * (Decimal("1") + Decimal("10") / Decimal("10000"))
        assert fill == expected

    def test_fallback_uses_min_bps_when_volume_zero(self):
        """bar_volume=0 falls back to spread or min_bps."""
        config = VolumeShareSlippageConfig(fallback_min_bps=Decimal("8"))
        model = VolumeShareSlippageModel(config=config)
        ctx = _ctx(qty="100", volume="0")  # zero volume
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        # Should not be 1 bps (old fixed-rate default)
        assert fill > Decimal("100") * (Decimal("1") + Decimal("0.0001"))

    def test_higher_ohlc_range_produces_higher_fallback_slippage(self):
        """Wider spread → higher cost when falling back."""
        model = VolumeShareSlippageModel()
        market_price = Decimal("100")
        narrow_ctx = _ctx(qty="100", high="100.1", low="99.9", close="100")
        wide_ctx = _ctx(qty="100", high="103", low="97", close="100")

        narrow_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=narrow_ctx
        )
        wide_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=wide_ctx
        )

        assert wide_fill > narrow_fill

    def test_unsupported_side_raises(self):
        model = VolumeShareSlippageModel()
        with pytest.raises(ValueError, match="unsupported side"):
            model.calculate_fill_price(side="INVALID", market_price=Decimal("100"))  # type: ignore[arg-type]

    def test_config_summary_contains_model_type(self):
        model = VolumeShareSlippageModel()
        summary = model.config_summary()
        assert summary["model_type"] == "volume_share"
        assert "impact_coefficient_bps" in summary
        assert "max_volume_share" in summary
        assert "fallback_min_bps" in summary

    def test_config_summary_values_are_strings(self):
        model = VolumeShareSlippageModel()
        summary = model.config_summary()
        for v in summary.values():
            assert isinstance(v, str)

    def test_determinism(self):
        """Same inputs always produce the same fill price."""
        model = VolumeShareSlippageModel()
        ctx = _ctx(qty="500", volume="5000")
        p1 = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("50"), context=ctx)
        p2 = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("50"), context=ctx)
        assert p1 == p2
