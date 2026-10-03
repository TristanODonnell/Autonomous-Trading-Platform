"""Shared sizing rule for research re-sims and the trading cycle (step 5c-E, F3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.position_sizer import PositionSizer
from autonomous_trading_platform.execution.services.sleeve_sizing import (
    VOL_LOOKBACK_BARS,
    position_budget_usd,
)
from autonomous_trading_platform.execution.services.volatility_scaling_service import (
    VolatilityScalingService,
)
from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider
from autonomous_trading_platform.research.simulation.services.simple_position_sizer import (
    SimplePositionSizer,
)

# A choppy series: realized vol well above the 15 % target, so the scalar is < 1.
_CHOPPY = [100.0 * (1.02 if i % 2 else 0.98) for i in range(VOL_LOOKBACK_BARS)]


@dataclass
class _Allocation:
    allocated_capital_usd: float = 60_000.0
    max_position_size_usd: float | None = None
    max_drawdown_allowed: float = 0.5


class _Provider:
    def get_allocation(self, **_kwargs) -> _Allocation:
        return _Allocation()


def _buy(symbol: str) -> Signal:
    ts = datetime(2024, 1, 2, 15, tzinfo=UTC)
    return Signal(
        signal_id=uuid4(),
        run_id=uuid4(),
        timestamp=ts,
        bar_timestamp=ts,
        strategy_id="s",
        symbol=symbol,
        direction=SignalDirection.BUY,
    )


def test_position_budget_is_an_equal_split() -> None:
    assert position_budget_usd(Decimal("60000"), 4) == Decimal("15000")
    assert position_budget_usd(Decimal("60000"), None) == Decimal("60000")
    with pytest.raises(ValueError):
        position_budget_usd(Decimal("60000"), 0)


def test_platform_sizer_splits_the_allocation_across_the_universe() -> None:
    sizer = PositionSizer(portfolio_engine=cast(IAllocationProvider, _Provider()))

    split = sizer.compute_quantity(
        strategy_id="s", symbol="AAA", current_price=Decimal("100"), symbol_count=4
    )
    whole = sizer.compute_quantity(strategy_id="s", symbol="AAA", current_price=Decimal("100"))

    assert split.base_notional == Decimal("15000")
    assert split.quantity == 150
    assert whole.quantity == 600  # legacy single-strategy mode: whole allocation


def test_research_and_platform_size_a_buy_identically() -> None:
    vol = VolatilityScalingService()
    scalar = vol.compute_scalar(symbol="AAA", closes=_CHOPPY)
    assert scalar is not None and scalar < 1

    platform = PositionSizer(portfolio_engine=cast(IAllocationProvider, _Provider()))
    platform_qty = platform.compute_quantity(
        strategy_id="s",
        symbol="AAA",
        current_price=Decimal("97.31"),
        combined_scalar=scalar,
        symbol_count=4,
    ).quantity

    research = SimplePositionSizer(
        total_capital=60_000.0, universe_size=4, volatility_scaling_service=vol
    )
    research_qty = research.compute_targets(
        signals=[_buy("AAA")], prices={"AAA": 97.31}, recent_closes={"AAA": _CHOPPY}
    )["AAA"]

    assert research_qty == platform_qty
    assert research_qty < int(Decimal("15000") / Decimal("97.31"))


def test_research_sizer_without_closes_is_unscaled() -> None:
    sizer = SimplePositionSizer(
        total_capital=60_000.0,
        universe_size=4,
        volatility_scaling_service=VolatilityScalingService(),
    )
    assert sizer.compute_targets(signals=[_buy("AAA")], prices={"AAA": 100.0}) == {"AAA": 150}


def test_research_sizer_compounds_with_equity_and_respects_the_symbol_cap() -> None:
    sizer = SimplePositionSizer(total_capital=60_000.0, universe_size=4)
    grown = sizer.compute_targets(
        signals=[_buy("AAA")], prices={"AAA": 100.0}, capital_scale=Decimal("1.1")
    )
    assert grown == {"AAA": 165}

    capped = SimplePositionSizer(
        total_capital=60_000.0, universe_size=4, max_symbol_exposure_usd=10_000.0
    )
    assert capped.compute_targets(signals=[_buy("AAA")], prices={"AAA": 100.0}) == {"AAA": 100}
