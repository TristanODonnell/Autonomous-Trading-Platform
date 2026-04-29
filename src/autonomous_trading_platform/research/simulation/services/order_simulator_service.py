from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)


class OrderSimulatorService:
    def __init__(
        self,
        portfolio_construction_service: PortfolioConstructionService,
    ) -> None:
        self.portfolio_construction_service = portfolio_construction_service

    def generate_order_intents(
        self,
        *,
        signals: list[Signal],
        positions: dict[str, Position],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
    ) -> list[Any]:
        order_intents = list(
            self.portfolio_construction_service.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=timestamp,
                now=timestamp,
            )
        )

        return [
            intent
            for intent in order_intents
            if self._is_valid_order_intent(intent=intent, prices=prices)
        ]

    def _is_valid_order_intent(
        self,
        *,
        intent: Any,
        prices: dict[str, float],
    ) -> bool:
        if intent.symbol not in prices:
            return False

        if getattr(intent, "qty", None) is None:
            return False

        if intent.qty <= 0:
            return False

        if getattr(intent, "side", None) is None:
            return False

        return getattr(intent, "order_type", None) is not None
