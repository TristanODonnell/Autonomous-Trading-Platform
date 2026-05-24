"""Integration tests: cost model wiring through SimulationCostModelService."""

from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.models.spread_aware_slippage_model import (
    SpreadAwareSlippageModel,
)
from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
    VolumeShareSlippageConfig,
    VolumeShareSlippageModel,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)


def _service(slippage_model) -> SimulationCostModelService:
    return SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0"), min_commission=Decimal("0")
        ),
        slippage_model=slippage_model,
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


class TestSimulationCostModelServicePluggable:
    def test_accepts_fixed_rate_model(self):
        svc = _service(SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.001"))))
        costs = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("10")
        )
        assert costs.fill_price == Decimal("100.100")

    def test_accepts_volume_share_model(self):
        svc = _service(VolumeShareSlippageModel())
        ctx = _ctx(qty="100", volume="10000")
        costs = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("100"), context=ctx
        )
        assert costs.fill_price > Decimal("100")

    def test_accepts_spread_aware_model(self):
        svc = _service(SpreadAwareSlippageModel())
        ctx = _ctx(qty="100", high="102", low="98", close="100")
        costs = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("100"), context=ctx
        )
        assert costs.fill_price > Decimal("100")

    def test_volume_share_is_invoked_with_bar_context(self):
        """Verify volume-share model actually uses bar volume."""
        config = VolumeShareSlippageConfig(
            impact_coefficient_bps=Decimal("100"),
            max_volume_share=Decimal("1.0"),
        )
        svc = _service(VolumeShareSlippageModel(config=config))

        # qty=10, volume=100 → share=0.1 → bps=10 → rate=0.001
        ctx = _ctx(qty="10", volume="100")
        costs = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("200"), quantity=Decimal("10"), context=ctx
        )
        expected_fill = Decimal("200") * (Decimal("1") + Decimal("10") / Decimal("10000"))
        assert costs.fill_price == expected_fill

    def test_volume_share_output_differs_from_fixed_rate(self):
        """Volume-share and fixed-rate produce different slippage for the same trade."""
        market_price = Decimal("100")
        qty = Decimal("50")
        ctx = _ctx(qty="50", volume="500")

        fixed_svc = _service(SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.0001"))))
        vs_svc = _service(VolumeShareSlippageModel())

        fixed_costs = fixed_svc.apply_costs(
            side=Side.BUY, reference_price=market_price, quantity=qty
        )
        vs_costs = vs_svc.apply_costs(
            side=Side.BUY, reference_price=market_price, quantity=qty, context=ctx
        )

        # Volume share = 50/500 = 0.1 → 25 bps, much larger than 1 bps fixed rate
        assert vs_costs.slippage_notional > fixed_costs.slippage_notional

    def test_effective_slippage_rate_is_computed_from_actual_slippage(self):
        """slippage_rate in SimulatedTradeCosts is slippage_per_share / reference_price."""
        svc = _service(SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.002"))))
        costs = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("5")
        )
        assert costs.slippage_rate == Decimal("0.002")

    def test_effective_slippage_rate_matches_volume_share_model(self):
        config = VolumeShareSlippageConfig(
            impact_coefficient_bps=Decimal("50"),
            max_volume_share=Decimal("1.0"),
        )
        svc = _service(VolumeShareSlippageModel(config=config))
        ctx = _ctx(qty="10", volume="100")  # share=0.1 → bps=5 → rate=0.0005
        costs = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("10"), context=ctx
        )
        expected_rate = Decimal("0.0005")
        assert abs(costs.slippage_rate - expected_rate) < Decimal("0.000001")

    def test_cost_model_context_not_required(self):
        """apply_costs without context still works (uses model fallback)."""
        svc = _service(VolumeShareSlippageModel())
        costs = svc.apply_costs(
            side=Side.SELL, reference_price=Decimal("50"), quantity=Decimal("10")
        )
        assert costs.fill_price < Decimal("50")

    def test_slippage_model_summary_delegates_to_config_summary(self):
        model = VolumeShareSlippageModel()
        svc = SimulationCostModelService(
            config=SimulationCostModelConfig(),
            slippage_model=model,
        )
        assert svc.slippage_model.config_summary() == model.config_summary()


class TestFixedRateModelCompatibility:
    def test_fixed_rate_still_works_as_explicit_mode(self):
        model = SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.0005")))
        svc = _service(model)
        ctx = _ctx(qty="100", volume="10000")
        # With context provided, fixed model ignores it
        costs_with_ctx = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("100"), context=ctx
        )
        costs_no_ctx = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("100")
        )
        assert costs_with_ctx.fill_price == costs_no_ctx.fill_price

    def test_fixed_rate_slippage_is_constant_regardless_of_volume(self):
        model = SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.001")))
        svc = _service(model)
        ctx_thin = _ctx(qty="100", volume="100")
        ctx_liquid = _ctx(qty="100", volume="1000000")

        costs_thin = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("100"), context=ctx_thin
        )
        costs_liquid = svc.apply_costs(
            side=Side.BUY,
            reference_price=Decimal("100"),
            quantity=Decimal("100"),
            context=ctx_liquid,
        )

        assert costs_thin.fill_price == costs_liquid.fill_price
