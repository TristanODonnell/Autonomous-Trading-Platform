from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pandas as pd

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.order_simulator_service import (
    OrderSimulatorService,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)


@dataclass(slots=True)
class SimulationExecutionResult:
    trade_logs: pd.DataFrame
    equity_curve: pd.DataFrame
    per_bar_metrics: pd.DataFrame
    positions: pd.DataFrame


class SimulationExecutionEngine:
    def __init__(
        self,
        *,
        cash_ledger_service: CashLedgerService,
        position_ledger_service: PositionLedgerService,
        lookahead_guard_service: LookaheadGuardService,
        order_simulator_service: OrderSimulatorService,
    ):
        self.cash_ledger_service = cash_ledger_service
        self.position_ledger_service = position_ledger_service
        self.lookahead_guard_service = lookahead_guard_service
        self.order_simulator_service = order_simulator_service

    def execute(
        self,
        *,
        run_id: UUID,
        strategy: Any,
        window: Any,
        context_builder: StrategyContextBuilder,
        simulated_execution_service: Any,
        initial_cash: float,
    ) -> SimulationExecutionResult:
        cash = Decimal(str(initial_cash))
        positions: dict[str, Position] = {}

        realized_pnl_by_symbol: dict[str, Decimal] = {}

        trade_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []
        position_rows: list[dict[str, Any]] = []

        strategy_state: dict[str, Any] = {}

        timeline = list(window.timeline)

        self.lookahead_guard_service.assert_timeline_strictly_increasing(
            timeline=timeline,
        )

        for timestamp in timeline:
            bars_at_timestamp = window.bars_by_timestamp[timestamp]

            signals = self._evaluate_signals(
                run_id=run_id,
                strategy=strategy,
                window=window,
                context_builder=context_builder,
                timestamp=timestamp,
                positions=positions,
                strategy_state=strategy_state,
            )

            prices = self._extract_prices(bars_at_timestamp)

            order_intents = self._construct_orders(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy.strategy_id,
                timestamp=timestamp,
            )

            fills = self._simulate_fills(
                order_intents=order_intents,
                bars_at_timestamp=bars_at_timestamp,
                simulated_execution_service=simulated_execution_service,
            )

            cash = self._apply_fills(
                run_id=run_id,
                timestamp=timestamp,
                fills=fills,
                positions=positions,
                cash=cash,
                prices=prices,
                realized_pnl_by_symbol=realized_pnl_by_symbol,
            )

            position_rows.extend(
                self._build_position_rows(
                    run_id=run_id,
                    strategy_id=strategy.strategy_id,
                    timestamp=timestamp,
                    positions=positions,
                    prices=prices,
                    realized_pnl_by_symbol=realized_pnl_by_symbol,
                )
            )

            trade_rows.extend(
                self._build_trade_rows(
                    fills=fills,
                    strategy_id=strategy.strategy_id,
                )
            )

            equity_rows.append(
                self._build_equity_row(
                    run_id=run_id,
                    strategy_id=strategy.strategy_id,
                    timestamp=timestamp,
                    cash=cash,
                    positions=positions,
                    prices=prices,
                )
            )

            metric_rows.extend(
                self._record_metrics(
                    run_id=run_id,
                    strategy_id=strategy.strategy_id,
                    timestamp=timestamp,
                    cash=cash,
                    positions=positions,
                    prices=prices,
                    bars_at_timestamp=bars_at_timestamp,
                    signals=signals,
                    fills=fills,
                    realized_pnl_by_symbol=realized_pnl_by_symbol,
                )
            )

        return SimulationExecutionResult(
            trade_logs=pd.DataFrame(trade_rows)
            if trade_rows
            else pd.DataFrame(
                columns=[
                    "run_id",
                    "strategy_id",
                    "symbol",
                    "timestamp",
                    "side",
                    "quantity",
                    "price",
                    "notional",
                    "fees",
                    "slippage",
                ]
            ),
            equity_curve=pd.DataFrame(equity_rows),
            per_bar_metrics=pd.DataFrame(metric_rows),
            positions=pd.DataFrame(position_rows)
            if position_rows
            else pd.DataFrame(
                columns=[
                    "run_id",
                    "strategy_id",
                    "symbol",
                    "timestamp",
                    "quantity",
                    "avg_cost",
                    "market_price",
                    "market_value",
                    "unrealized_pnl",
                    "realized_pnl",
                ]
            ),
        )

    def _evaluate_signals(
        self,
        *,
        run_id: UUID,
        strategy: Any,
        window: Any,
        context_builder: Any,
        timestamp: datetime,
        positions: dict[str, Position],
        strategy_state: dict[str, Any],
    ) -> list[Signal]:
        signals: list[Signal] = []

        for symbol in window.symbols:
            context = context_builder.build_from_window(
                run_id=run_id,
                strategy_id=strategy.strategy_id,
                symbol=symbol,
                timestamp=timestamp,
                window=window,
                positions=positions,
                state=strategy_state,
            )

            if context is not None:
                print(f"{timestamp} | {symbol} | bars={len(context.bars)}")

            if context is None:
                continue

            self.lookahead_guard_service.assert_historical_only(
                symbol=symbol,
                simulation_timestamp=timestamp,
                bars=context.bars,
            )

            signal = strategy.evaluate_symbol(context)

            if signal is not None:
                signals.append(signal)

        return signals

    def _construct_orders(
        self,
        *,
        signals: list[Signal],
        positions: dict[str, Position],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
    ) -> list[Any]:
        return self.order_simulator_service.generate_order_intents(
            signals=signals,
            positions=positions,
            prices=prices,
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=timestamp,
        )

    def _simulate_fills(
        self,
        *,
        order_intents: list[Any],
        bars_at_timestamp: dict[str, Any],
        simulated_execution_service: Any,
    ) -> list[Any]:
        return list(
            simulated_execution_service.fill(
                order_intents=order_intents,
                bars_at_timestamp=bars_at_timestamp,
            )
        )

    def _apply_fills(
        self,
        *,
        run_id: UUID,
        timestamp: datetime,
        fills: list[Any],
        positions: dict[str, Position],
        cash: Decimal,
        prices: dict[str, float],
        realized_pnl_by_symbol: dict[str, Decimal],
    ) -> Decimal:
        updated_cash = cash

        for fill in fills:
            cash_snapshot = CashSnapshot(
                snapshot_id=uuid4(),
                run_id=run_id,
                timestamp=timestamp,
                currency="USD",
                cash=updated_cash,
                buying_power=updated_cash,
                reserved_cash=Decimal("0"),
                equity=self._calculate_equity(
                    cash=updated_cash,
                    positions=positions,
                    prices=prices,
                ),
                source=OrderSource.SIMULATION,
                capital_bucket=None,
            )

            cash_result = self.cash_ledger_service.apply_fill(
                existing_snapshot=cash_snapshot,
                fill=fill,
            )
            updated_cash = cash_result.cash

            market_price = Decimal(str(prices.get(fill.symbol, fill.price)))

            position_result = self.position_ledger_service.apply_fill(
                existing_position=positions.get(fill.symbol),
                fill=fill,
                market_price=market_price,
            )

            if position_result.updated_position is None:
                positions.pop(fill.symbol, None)
            else:
                positions[fill.symbol] = position_result.updated_position

            realized_pnl_by_symbol[fill.symbol] = (
                realized_pnl_by_symbol.get(fill.symbol, Decimal("0")) + position_result.realized_pnl
            )

        return updated_cash

    def _record_metrics(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
        cash: Decimal,
        positions: dict[str, Position],
        prices: dict[str, float],
        bars_at_timestamp: dict[str, Any],
        signals: list[Signal],
        fills: list[Any],
        realized_pnl_by_symbol: dict[str, Decimal],
    ) -> list[dict[str, Any]]:

        rows: list[dict[str, Any]] = []
        for symbol in bars_at_timestamp:
            position = positions.get(symbol)

            position_qty = Decimal(position.quantity) if position else Decimal("0")
            unrealized_pnl = (
                Decimal(position.unrealized_pnl)
                if position is not None and position.unrealized_pnl is not None
                else Decimal("0")
            )
            realized_pnl = realized_pnl_by_symbol.get(symbol, Decimal("0"))

            rows.append(
                {
                    "run_id": str(run_id),
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "bar_return": 0.0,
                    "position_size": float(position_qty),
                    "unrealized_pnl": float(unrealized_pnl),
                    "realized_pnl": float(realized_pnl),
                }
            )

        return rows

    def _extract_prices(self, bars_at_timestamp: dict[str, Any]) -> dict[str, float]:
        return {symbol: float(bar.close) for symbol, bar in bars_at_timestamp.items()}

    def _calculate_equity(
        self,
        *,
        cash: Decimal,
        positions: dict[str, Position],
        prices: dict[str, float],
    ) -> Decimal:
        return cash + self._calculate_positions_value(
            positions=positions,
            prices=prices,
        )

    def _build_equity_row(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
        cash: Decimal,
        positions: dict[str, Position],
        prices: dict[str, float],
    ) -> dict[str, Any]:
        positions_value = self._calculate_positions_value(
            positions=positions,
            prices=prices,
        )

        equity = cash + positions_value

        return {
            "run_id": str(run_id),
            "strategy_id": strategy_id,
            "timestamp": timestamp,
            "equity": float(equity),
            "cash": float(cash),
            "positions_value": float(positions_value),
            "drawdown": 0.0,
        }

    def _calculate_positions_value(
        self,
        *,
        positions: dict[str, Position],
        prices: dict[str, float],
    ) -> Decimal:
        positions_value = Decimal("0")

        for position in positions.values():
            price = Decimal(str(prices.get(position.symbol, position.market_price)))
            positions_value += Decimal(position.quantity) * price

        return positions_value

    def _build_trade_rows(
        self,
        *,
        fills: list[Any],
        strategy_id: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for fill in fills:
            rows.append(
                {
                    "run_id": str(fill.run_id),
                    "strategy_id": strategy_id,
                    "symbol": fill.symbol,
                    "timestamp": fill.timestamp,
                    "side": fill.side.value,
                    "quantity": float(fill.quantity),
                    "price": float(fill.price),
                    "notional": float(Decimal(str(fill.quantity)) * Decimal(str(fill.price))),
                    "fees": float(fill.fees or 0),
                    "slippage": 0.0,
                }
            )

        return rows

    def _build_position_rows(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
        positions: dict[str, Position],
        prices: dict[str, float],
        realized_pnl_by_symbol: dict[str, Decimal],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for symbol, position in positions.items():
            market_price = Decimal(str(prices.get(symbol, position.market_price)))
            quantity = Decimal(position.quantity)
            market_value = quantity * market_price

            rows.append(
                {
                    "run_id": str(run_id),
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "quantity": float(quantity),
                    "avg_cost": float(position.avg_cost or 0),
                    "market_price": float(market_price),
                    "market_value": float(market_value),
                    "unrealized_pnl": float(position.unrealized_pnl or 0),
                    "realized_pnl": float(realized_pnl_by_symbol.get(symbol, Decimal("0"))),
                }
            )

        return rows
