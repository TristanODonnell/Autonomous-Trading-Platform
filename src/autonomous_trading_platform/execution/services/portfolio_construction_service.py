# autonomous_trading_platform/execution/services/portfolio_construction_service.py

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    SignalDirection,
    TimeInForce,
)
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.position_sizer import PositionSizer
from autonomous_trading_platform.goverance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.exceptions import (
    AllocationDeniedError,
    NoPolicyFoundError,
)
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService

logger = logging.getLogger(__name__)


class PortfolioConstructionService:
    def __init__(
        self,
        pre_trade_risk_service: PreTradeRiskService,
        position_sizer: PositionSizer,
    ) -> None:
        self.pre_trade_risk_service = pre_trade_risk_service
        self._position_sizer = position_sizer

    def generate_order_intents(
        self,
        signals: list[Signal],
        positions: Mapping[str, int | Position],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        approval_status: GovernanceState,
        bar_timestamp: UTCDateTime,
        now: datetime,
        performance_tier: str | None = None,
    ):
        """
        Generate order intents from signals.

        approval_status is required so the position sizer can resolve
        the correct allocation policy from PortfolioEngine.
        """
        target_positions = self.position_sizer(
            signals=signals,
            prices=prices,
            strategy_id=strategy_id,
            approval_status=approval_status,
            performance_tier=performance_tier,
        )
        deltas = self.calculate_deltas(positions, target_positions)

        for delta in deltas:
            order_intent = self.build_order_intent(
                delta=delta,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
            )
            self.pre_trade_risk_service.assert_order_allowed(order_intent, now=now)
            yield order_intent

    def position_sizer(
        self,
        signals: list[Signal],
        prices: dict[str, float],
        strategy_id: str,
        approval_status: GovernanceState,
        performance_tier: str | None = None,
    ) -> dict[str, int]:
        """
        Convert signals into target whole-share quantities using the allocation
        resolved from PortfolioEngine.

        BUY signals → compute quantity from allocated capital + current price.
        SELL / FLAT signals → target 0 (close the position).

        Symbols where sizing fails (insufficient allocation, missing price,
        policy not found) are skipped with a warning rather than crashing
        the whole cycle.
        """
        target_positions: dict[str, int] = {}

        for signal in signals:
            if signal.direction in (SignalDirection.SELL, SignalDirection.FLAT):
                target_positions[signal.symbol] = 0
                continue

            if signal.direction != SignalDirection.BUY:
                # Unknown direction — skip defensively
                logger.warning(
                    "position_sizer.unknown_direction",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": signal.symbol,
                        "direction": str(signal.direction),
                    },
                )
                target_positions[signal.symbol] = 0
                continue

            # BUY — resolve quantity from allocation
            raw_price = prices.get(signal.symbol)
            if raw_price is None:
                logger.warning(
                    "position_sizer.missing_price",
                    extra={"strategy_id": strategy_id, "symbol": signal.symbol},
                )
                target_positions[signal.symbol] = 0
                continue

            current_price = Decimal(str(raw_price))

            try:
                quantity = self._position_sizer.compute_quantity(
                    strategy_id=strategy_id,
                    approval_status=approval_status,
                    symbol=signal.symbol,
                    current_price=current_price,
                    performance_tier=performance_tier,
                )
            except (AllocationDeniedError, NoPolicyFoundError) as exc:
                # Governance / policy issue — skip this symbol, don't crash the cycle
                logger.warning(
                    "position_sizer.allocation_skipped",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": signal.symbol,
                        "reason": str(exc),
                    },
                )
                target_positions[signal.symbol] = 0
                continue

            target_positions[signal.symbol] = quantity

        return target_positions

    def calculate_deltas(
        self,
        current_positions: Mapping[str, int | Position],
        target_positions: dict[str, int],
    ) -> list[dict[str, int | str]]:
        deltas: list[dict[str, int | str]] = []

        all_symbols = sorted(set(current_positions) | set(target_positions))
        for symbol in all_symbols:
            current_position = current_positions.get(symbol, 0)

            if hasattr(current_position, "quantity"):
                current_qty = int(current_position.quantity)
            else:
                current_qty = int(current_position)

            target_qty = target_positions.get(symbol, 0)
            delta_qty = target_qty - current_qty

            if delta_qty != 0:
                deltas.append(
                    {
                        "symbol": symbol,
                        "current_qty": current_qty,
                        "target_qty": target_qty,
                        "delta_qty": delta_qty,
                    }
                )

        return deltas

    def build_order_intent(
        self,
        delta: dict[str, Any],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        now: datetime,
    ) -> OrderIntent:
        symbol = str(delta["symbol"])
        delta_qty = int(delta["delta_qty"])

        side = Side.BUY if delta_qty > 0 else Side.SELL
        qty = abs(delta_qty)
        price = Decimal(str(prices[symbol]))

        client_order_id = self._build_client_order_id(
            run_id=run_id,
            strategy_id=strategy_id,
            bar_timestamp=bar_timestamp,
            symbol=symbol,
            side=side,
            qty=qty,
        )
        intent_id = self._build_intent_id(client_order_id=client_order_id)

        return OrderIntent(
            intent_id=intent_id,
            idempotency_key=client_order_id,
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=now,
            bar_timestamp=bar_timestamp,
            symbol=symbol,
            side=side,
            qty=qty,
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=price,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=client_order_id,
            metadata=None,
        )

    @staticmethod
    def _build_client_order_id(
        *,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        symbol: str,
        side: Side,
        qty: int,
    ) -> str:
        seed = (
            f"run_id={run_id}|"
            f"strategy_id={strategy_id}|"
            f"bar_timestamp={bar_timestamp.isoformat()}|"
            f"symbol={symbol}|"
            f"side={side.value}|"
            f"qty={qty}"
        )
        deterministic_uuid = uuid5(NAMESPACE_URL, seed)
        return f"{strategy_id}-{symbol}-{deterministic_uuid.hex[:16]}"

    @staticmethod
    def _build_intent_id(*, client_order_id: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"order-intent:{client_order_id}")
