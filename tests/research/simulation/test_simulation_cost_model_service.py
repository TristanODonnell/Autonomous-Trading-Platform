from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)


def test_cost_model_applies_buy_slippage_above_reference_price():
    service = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0.01"),
            min_commission=Decimal("1.00"),
            slippage_bps=Decimal("10"),
        )
    )

    costs = service.apply_costs(
        side=Side.BUY,
        reference_price=Decimal("100"),
        quantity=Decimal("10"),
    )

    assert costs.fill_price == Decimal("100.100")
    assert costs.slippage_notional == Decimal("1.000")
    assert costs.commission == Decimal("1.00")


def test_cost_model_applies_sell_slippage_below_reference_price():
    service = SimulationCostModelService(
        config=SimulationCostModelConfig(slippage_bps=Decimal("10"))
    )

    costs = service.apply_costs(
        side=Side.SELL,
        reference_price=Decimal("100"),
        quantity=Decimal("10"),
    )

    assert costs.fill_price == Decimal("99.900")
    assert costs.slippage_notional == Decimal("1.000")
