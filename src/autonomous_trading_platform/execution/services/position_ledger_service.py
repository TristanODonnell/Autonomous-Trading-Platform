from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.fill import Fill

ZERO = Decimal("0")


@dataclass
class PositionLedgerResult:
    updated_position: Position | None
    realized_pnl: Decimal
    closed_quantity: Decimal


class PositionLedgerService:
    def apply_fill(
        self,
        existing_position: Position | None,
        fill: Fill,
        market_price: Decimal | None = None,
    ) -> PositionLedgerResult:
        quantity = Decimal(fill.quantity)
        price = Decimal(fill.price)
        side = fill.side

        if quantity <= ZERO:
            raise ValueError("fill.quantity must be positive")
        if price <= ZERO:
            raise ValueError("fill.price must be positive")

        mark_price = Decimal(market_price) if market_price is not None else price

        if existing_position is None:
            if side != Side.BUY:
                raise ValueError("cannot sell without an existing long position in v1")

            new_position = self._build_position(
                symbol=fill.symbol,
                quantity=quantity,
                avg_cost=price,
                market_price=mark_price,
            )
            return PositionLedgerResult(
                updated_position=new_position,
                realized_pnl=ZERO,
                closed_quantity=ZERO,
            )

        existing_quantity = Decimal(existing_position.quantity)
        existing_avg_cost = (
            Decimal(existing_position.avg_cost) if existing_position.avg_cost is not None else ZERO
        )

        if existing_quantity < ZERO:
            raise NotImplementedError("short positions are not supported in v1")

        if side == Side.BUY:
            new_quantity = existing_quantity + quantity
            new_avg_cost = (
                (existing_quantity * existing_avg_cost) + (quantity * price)
            ) / new_quantity

            new_position = self._build_position(
                symbol=existing_position.symbol,
                quantity=new_quantity,
                avg_cost=new_avg_cost,
                market_price=mark_price,
            )
            return PositionLedgerResult(
                updated_position=new_position,
                realized_pnl=ZERO,
                closed_quantity=ZERO,
            )

        if side == Side.SELL:
            if quantity > existing_quantity:
                raise ValueError("selling through zero is not supported in v1")

            closed_quantity = quantity
            realized_pnl = (price - existing_avg_cost) * closed_quantity
            remaining_quantity = existing_quantity - quantity

            if remaining_quantity == ZERO:
                return PositionLedgerResult(
                    updated_position=None,
                    realized_pnl=realized_pnl,
                    closed_quantity=closed_quantity,
                )

            new_position = self._build_position(
                symbol=existing_position.symbol,
                quantity=remaining_quantity,
                avg_cost=existing_avg_cost,
                market_price=mark_price,
            )
            return PositionLedgerResult(
                updated_position=new_position,
                realized_pnl=realized_pnl,
                closed_quantity=closed_quantity,
            )

        raise ValueError(f"unsupported fill side: {side}")

    def _build_position(
        self,
        symbol: str,
        quantity: Decimal,
        avg_cost: Decimal,
        market_price: Decimal,
    ) -> Position:
        market_value = quantity * market_price
        unrealized_pnl = (market_price - avg_cost) * quantity

        return Position(
            symbol=symbol,
            quantity=quantity,
            avg_cost=avg_cost,
            market_price=market_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
        )
