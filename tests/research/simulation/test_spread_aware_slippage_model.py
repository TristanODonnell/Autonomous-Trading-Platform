"""Tests for SpreadAwareSlippageModel."""

from __future__ import annotations

from decimal import Decimal

import pytest

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.cost_model_type import CostModelType
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext
from autonomous_trading_platform.research.simulation.models.spread_aware_slippage_model import (
    SpreadAwareSlippageConfig,
    SpreadAwareSlippageModel,
)


def _ctx(*, high: str, low: str, close: str, qty: str = "100") -> SlippageContext:
    return SlippageContext(
        quantity=Decimal(qty),
        bar_high=Decimal(high),
        bar_low=Decimal(low),
        bar_close=Decimal(close),
    )


class TestSpreadAwareSlippageModel:
    def test_model_type_is_spread_aware(self):
        model = SpreadAwareSlippageModel()
        assert model.model_type == CostModelType.SPREAD_AWARE

    def test_buy_fill_price_above_market(self):
        model = SpreadAwareSlippageModel()
        ctx = _ctx(high="101", low="99", close="100")
        price = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        assert price > Decimal("100")

    def test_sell_fill_price_below_market(self):
        model = SpreadAwareSlippageModel()
        ctx = _ctx(high="101", low="99", close="100")
        price = model.calculate_fill_price(side=Side.SELL, market_price=Decimal("100"), context=ctx)
        assert price < Decimal("100")

    def test_wider_spread_produces_higher_slippage(self):
        """Higher OHLC range → higher slippage cost."""
        model = SpreadAwareSlippageModel()
        market_price = Decimal("100")
        narrow = _ctx(high="100.2", low="99.8", close="100")
        wide = _ctx(high="104", low="96", close="100")

        narrow_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=narrow
        )
        wide_fill = model.calculate_fill_price(
            side=Side.BUY, market_price=market_price, context=wide
        )

        assert wide_fill > narrow_fill

    def test_spread_formula(self):
        """half_spread_bps = (high-low)/close/2 * 10000; verify numerically."""
        config = SpreadAwareSlippageConfig(
            min_half_spread_bps=Decimal("0"),
            max_half_spread_bps=Decimal("10000"),
            spread_multiplier=Decimal("1"),
        )
        model = SpreadAwareSlippageModel(config=config)
        # (104-96)/100/2 * 10000 = 400 bps
        ctx = _ctx(high="104", low="96", close="100")
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        expected = Decimal("100") * (Decimal("1") + Decimal("400") / Decimal("10000"))
        assert fill == expected

    def test_clamped_at_max_half_spread(self):
        config = SpreadAwareSlippageConfig(
            min_half_spread_bps=Decimal("5"),
            max_half_spread_bps=Decimal("50"),
        )
        model = SpreadAwareSlippageModel(config=config)
        # Extreme spread = 2000 bps raw, clamped to 50
        ctx = _ctx(high="200", low="0.01", close="100")
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        expected_max = Decimal("100") * (Decimal("1") + Decimal("50") / Decimal("10000"))
        assert fill == expected_max

    def test_clamped_at_min_half_spread(self):
        config = SpreadAwareSlippageConfig(
            min_half_spread_bps=Decimal("10"),
            max_half_spread_bps=Decimal("100"),
        )
        model = SpreadAwareSlippageModel(config=config)
        # Very tight spread = 0.01 bps raw, clamped to 10
        ctx = _ctx(high="100.001", low="99.999", close="100")
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        expected_min = Decimal("100") * (Decimal("1") + Decimal("10") / Decimal("10000"))
        assert fill == expected_min

    def test_spread_multiplier_scales_slippage(self):
        config_1x = SpreadAwareSlippageConfig(spread_multiplier=Decimal("1"))
        config_2x = SpreadAwareSlippageConfig(spread_multiplier=Decimal("2"))
        ctx = _ctx(high="102", low="98", close="100")

        fill_1x = SpreadAwareSlippageModel(config=config_1x).calculate_fill_price(
            side=Side.BUY, market_price=Decimal("100"), context=ctx
        )
        fill_2x = SpreadAwareSlippageModel(config=config_2x).calculate_fill_price(
            side=Side.BUY, market_price=Decimal("100"), context=ctx
        )

        assert fill_2x > fill_1x

    def test_fallback_to_min_when_no_context(self):
        config = SpreadAwareSlippageConfig(min_half_spread_bps=Decimal("7"))
        model = SpreadAwareSlippageModel(config=config)
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=None)
        expected = Decimal("100") * (Decimal("1") + Decimal("7") / Decimal("10000"))
        assert fill == expected

    def test_fallback_to_min_when_close_is_zero(self):
        config = SpreadAwareSlippageConfig(min_half_spread_bps=Decimal("5"))
        model = SpreadAwareSlippageModel(config=config)
        ctx = SlippageContext(
            quantity=Decimal("100"),
            bar_high=Decimal("1"),
            bar_low=Decimal("0"),
            bar_close=Decimal("0"),
        )
        fill = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        expected = Decimal("100") * (Decimal("1") + Decimal("5") / Decimal("10000"))
        assert fill == expected

    def test_unsupported_side_raises(self):
        model = SpreadAwareSlippageModel()
        with pytest.raises(ValueError, match="unsupported side"):
            model.calculate_fill_price(side="BAD", market_price=Decimal("100"))  # type: ignore[arg-type]

    def test_config_summary_contains_model_type(self):
        model = SpreadAwareSlippageModel()
        summary = model.config_summary()
        assert summary["model_type"] == "spread_aware"
        assert "min_half_spread_bps" in summary
        assert "max_half_spread_bps" in summary
        assert "spread_multiplier" in summary

    def test_config_summary_values_are_strings(self):
        model = SpreadAwareSlippageModel()
        for v in model.config_summary().values():
            assert isinstance(v, str)

    def test_determinism(self):
        model = SpreadAwareSlippageModel()
        ctx = _ctx(high="101", low="99", close="100")
        p1 = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        p2 = model.calculate_fill_price(side=Side.BUY, market_price=Decimal("100"), context=ctx)
        assert p1 == p2
