"""Execution-cost multiplier used by StressStage cost re-simulations."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis, Side
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)
from tests.utilities.factories import make_five_minute_bar


def _cost_service(rate: str = "0.001", commission: str = "0.01") -> SimulationCostModelService:
    return SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal(commission), min_commission=Decimal("1.00")
        ),
        slippage_model=SlippageModel(SlippageModelConfig(slippage_rate=Decimal(rate))),
    )


class TestApplyCostsMultiplier:
    @pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
    def test_multiplier_scales_slippage_and_commission(self, side: Side) -> None:
        svc = _cost_service()
        base = svc.apply_costs(side=side, reference_price=Decimal("100"), quantity=Decimal("500"))
        doubled = svc.apply_costs(
            side=side,
            reference_price=Decimal("100"),
            quantity=Decimal("500"),
            cost_multiplier=Decimal("2"),
        )
        assert doubled.slippage_per_share == base.slippage_per_share * 2
        assert doubled.slippage_per_share > 0  # still adverse on both sides
        assert doubled.commission == base.commission * 2
        assert doubled.total_cost == base.total_cost * 2

    def test_multiplier_one_is_identity(self) -> None:
        svc = _cost_service()
        base = svc.apply_costs(
            side=Side.BUY, reference_price=Decimal("100"), quantity=Decimal("10")
        )
        same = svc.apply_costs(
            side=Side.BUY,
            reference_price=Decimal("100"),
            quantity=Decimal("10"),
            cost_multiplier=Decimal("1"),
        )
        assert base == same

    def test_negative_multiplier_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost_multiplier"):
            _cost_service().apply_costs(
                side=Side.BUY,
                reference_price=Decimal("100"),
                quantity=Decimal("1"),
                cost_multiplier=Decimal("-1"),
            )


class TestExecutionServiceMultiplier:
    def _intent(self) -> SimpleNamespace:
        return SimpleNamespace(
            intent_id=UUID("11111111-0000-0000-0000-000000000001"),
            run_id=UUID("22222222-0000-0000-0000-000000000001"),
            symbol="AAPL",
            side=Side.BUY,
            qty=Decimal("10"),
            order_type="market",
        )

    def _fill_price(self, svc: SimulatedExecutionService) -> Decimal:
        bar = make_five_minute_bar(
            timestamp=datetime(2025, 3, 3, 14, 30, tzinfo=UTC),
            symbol="AAPL",
            open_price="100",
            high_price="100",
            low_price="100",
            close_price="100",
            volume=10_000,
        )
        batch = svc.fill(order_intents=[self._intent()], bars_at_timestamp={"AAPL": bar})
        return batch.fills[0].price

    def test_multiplier_is_reset_every_run(self) -> None:
        svc = SimulatedExecutionService(
            simulation_cost_model_service=_cost_service(),
            fill_model_config=SimulatedFillModelConfig(),
        )
        svc.reset_for_run(rng=random.Random(1))
        normal = self._fill_price(svc)
        svc.reset_for_run(rng=random.Random(1), cost_multiplier=3.0)
        stressed = self._fill_price(svc)
        # A stress run must not leak its multiplier into the next normal run.
        svc.reset_for_run(rng=random.Random(1))
        after = self._fill_price(svc)

        assert normal == Decimal("100.100")
        assert stressed == Decimal("100.300")
        assert after == normal

    def test_negative_multiplier_rejected(self) -> None:
        svc = SimulatedExecutionService(
            simulation_cost_model_service=_cost_service(),
            fill_model_config=SimulatedFillModelConfig(),
        )
        with pytest.raises(ValueError, match="cost_multiplier"):
            svc.reset_for_run(cost_multiplier=-0.5)


class TestRunIdentity:
    def _request(self, **kwargs) -> SimulationRunRequest:
        return SimulationRunRequest(
            strategy_id="s1",
            strategy_config={"type": "momentum"},
            dataset_version="v1",
            random_seed=1,
            price_basis=PriceBasis.RAW,
            symbols=["AAPL"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 1),
            **kwargs,
        )

    def test_default_multiplier_keeps_existing_run_id(self) -> None:
        derive = SimulationRunner._derive_run_id
        assert derive(None, self._request()) == derive(  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            self._request(cost_multiplier=1.0),
        )

    def test_stress_multiplier_gets_distinct_run_id(self) -> None:
        derive = SimulationRunner._derive_run_id
        assert derive(None, self._request()) != derive(  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            self._request(cost_multiplier=2.0),
        )
