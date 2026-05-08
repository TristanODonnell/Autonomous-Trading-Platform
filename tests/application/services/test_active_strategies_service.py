from __future__ import annotations

from decimal import Decimal
from typing import cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_strategies_service import (
    ActiveStrategiesService,
)
from autonomous_trading_platform.storage.sor.repositories.queries.active_strategies_repository import (
    ActiveStrategiesRepository,
    ActiveStrategyDashboardRow,
)


class FakeActiveStrategiesRepository:
    def __init__(self, rows: list[ActiveStrategyDashboardRow]) -> None:
        self.rows = rows
        self.list_called = False

    def list_active_strategies(self) -> list[ActiveStrategyDashboardRow]:
        self.list_called = True
        return self.rows


def test_returns_active_strategy_dashboard_rows() -> None:
    repository = FakeActiveStrategiesRepository(
        rows=[
            ActiveStrategyDashboardRow(
                strategy_id="momentum_v1",
                display_name="Momentum V1",
                strategy_type="momentum",
                status="paper",
                todays_return=Decimal("0.00"),
                trade_count_today=0,
                allocated_capital=Decimal("0.00"),
                enabled=True,
            ),
            ActiveStrategyDashboardRow(
                strategy_id="mean_reversion_v1",
                display_name="Mean Reversion V1",
                strategy_type="mean_reversion",
                status="live",
                todays_return=Decimal("0.00"),
                trade_count_today=0,
                allocated_capital=Decimal("0.00"),
                enabled=True,
            ),
        ]
    )
    service = ActiveStrategiesService(
        session=cast(Session, None),
        repository=cast(ActiveStrategiesRepository, repository),
    )

    result = service.list_active_strategies()

    assert result == [
        {
            "strategy_id": "momentum_v1",
            "display_name": "Momentum V1",
            "strategy_type": "momentum",
            "status": "paper",
            "todays_return": Decimal("0.00"),
            "trade_count_today": 0,
            "allocated_capital": Decimal("0.00"),
            "enabled": True,
        },
        {
            "strategy_id": "mean_reversion_v1",
            "display_name": "Mean Reversion V1",
            "strategy_type": "mean_reversion",
            "status": "live",
            "todays_return": Decimal("0.00"),
            "trade_count_today": 0,
            "allocated_capital": Decimal("0.00"),
            "enabled": True,
        },
    ]
    assert repository.list_called is True
