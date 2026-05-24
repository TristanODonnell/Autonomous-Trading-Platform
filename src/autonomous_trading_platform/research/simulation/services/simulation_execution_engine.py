from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pandas as pd

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import (
    OrderSource,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.simple_position_sizer import (
    SimplePositionSizer,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_model import (
    SimulatedExecutionModel,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)

logger = get_logger(__name__)

# Stable namespace for deterministic intent IDs (P-01).
_INTENT_NS = uuid5(NAMESPACE_URL, "autonomous-trading-platform:intent")


@dataclass(slots=True)
class SimulationExecutionResult:
    trade_logs: pd.DataFrame
    equity_curve: pd.DataFrame
    per_bar_metrics: pd.DataFrame
    positions: pd.DataFrame
    signal_log: pd.DataFrame


class SimulationExecutionEngine:
    def __init__(
        self,
        *,
        cash_ledger_service: CashLedgerService,
        position_ledger_service: PositionLedgerService,
        lookahead_guard_service: LookaheadGuardService,
        position_sizer: SimplePositionSizer,
        simulated_execution_model: SimulatedExecutionModel | None = None,
    ):
        self.cash_ledger_service = cash_ledger_service
        self.position_ledger_service = position_ledger_service
        self.lookahead_guard_service = lookahead_guard_service
        self.position_sizer = position_sizer
        self.simulated_execution_model = simulated_execution_model or SimulatedExecutionModel()

    def execute(
        self,
        *,
        run_id: UUID,
        strategy: Any,
        window: Any,
        context_builder: StrategyContextBuilder,
        simulated_execution_service: Any,
        initial_cash: float,
        execution_policy_config: ExecutionPolicyConfig | None = None,
    ) -> SimulationExecutionResult:
        cash = Decimal(str(initial_cash))
        # Aggregate cash reserved for all pending buy orders. Reduces buying power
        # immediately so the same cash cannot be committed to multiple orders.
        reserved_cash: Decimal = Decimal("0")
        positions: dict[str, Position] = {}
        realized_pnl_by_symbol: dict[str, Decimal] = {}

        trade_rows: list[dict[str, Any]] = []
        signal_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []
        position_rows: list[dict[str, Any]] = []

        strategy_state: dict[str, Any] = {}
        timeline = list(window.timeline)

        self.lookahead_guard_service.assert_timeline_strictly_increasing(timeline=timeline)

        latency_bars = simulated_execution_service.fill_model_config.latency_bars
        # Execution scheduler: maps target_bar_index → orders to execute on that bar.
        # latency_bars=0 → orders execute immediately (same bar, at close).
        # latency_bars=N → orders execute N bars later (at that bar's open).
        scheduled: dict[int, list[OrderIntent]] = {}

        for bar_index, timestamp in enumerate(timeline):
            bars_at_timestamp = window.bars_by_timestamp[timestamp]
            is_warmup = window.is_warmup(timestamp)

            # Phase 1: Execute any orders scheduled for this bar index (latency>=1 only).
            # These were generated N bars ago and execute at this bar's open price.
            deferred_fills: list[Any] = []
            if not is_warmup and bar_index in scheduled:
                deferred_intents = scheduled.pop(bar_index)
                deferred_fills, deferred_carry_forward = self._simulate_fills(
                    order_intents=deferred_intents,
                    bars_at_timestamp=bars_at_timestamp,
                    simulated_execution_service=simulated_execution_service,
                )
                # Release reservation for intents that were neither filled nor carried
                # forward (stochastic rejection or missing bar data).
                reserved_cash = self._release_lost_intents(
                    deferred_intents, deferred_fills, deferred_carry_forward, reserved_cash
                )
                # Partially filled orders carry their remaining quantity to the next bar.
                # Carry-forward intents keep their existing reservation in the pool —
                # they must NOT be re-reserved when added back to the schedule.
                if deferred_carry_forward:
                    scheduled.setdefault(bar_index + 1, []).extend(deferred_carry_forward)

            signals = self._evaluate_signals(
                run_id=run_id,
                strategy=strategy,
                window=window,
                context_builder=context_builder,
                timestamp=timestamp,
                positions=positions,
                strategy_state=strategy_state,
            )
            signal_rows.extend(
                self._build_signal_rows(
                    run_id=run_id,
                    strategy_id=strategy.strategy_id,
                    timestamp=timestamp,
                    signals=signals,
                )
            )

            # During warmup we only evaluate signals so indicators accumulate
            # state. No orders, fills, trades or equity rows are recorded.
            if is_warmup:
                continue

            prices = self._extract_prices(bars_at_timestamp)

            # Apply deferred fills so positions are current before mark-to-market.
            if deferred_fills:
                cash, reserved_cash = self._apply_fills(
                    run_id=run_id,
                    timestamp=timestamp,
                    fills=deferred_fills,
                    positions=positions,
                    cash=cash,
                    reserved_cash=reserved_cash,
                    prices=prices,
                    realized_pnl_by_symbol=realized_pnl_by_symbol,
                )

            order_intents = self._construct_orders(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy.strategy_id,
                timestamp=timestamp,
            )

            # Apply execution policy: convert parent intents to (bar_offset, child_intent)
            # pairs.  PASSTHROUGH returns [(0, intent)] per order — identical to the pre-policy
            # path.  TWAP/VWAP-lite spread slices over consecutive bars; LIMIT computes a
            # limit price from the current bar close.
            child_pairs = self._plan_child_orders(
                order_intents=order_intents,
                execution_policy_config=execution_policy_config,
                prices=prices,
            )

            # Phase 2: schedule child orders for execution.
            # latency_bars=0 → bar_offset=0 children execute immediately this bar (close price);
            #                   bar_offset>0 children schedule at bar_index+bar_offset.
            # latency_bars>=1 → all children defer to bar_index+latency_bars+bar_offset (open price).
            immediate_fills: list[Any] = []
            if latency_bars == 0:
                # Reserve and filter immediate child intents before attempting fills.
                immediate_intents: list[Any] = []
                for off, ci in child_pairs:
                    if off == 0:
                        cash, reserved_cash, accepted = self._try_reserve(ci, cash, reserved_cash)
                        if accepted:
                            immediate_intents.append(ci)

                if immediate_intents:
                    immediate_fills, immediate_carry_forward = self._simulate_fills(
                        order_intents=immediate_intents,
                        bars_at_timestamp=bars_at_timestamp,
                        simulated_execution_service=simulated_execution_service,
                    )
                    # Release reservation for rejected/no-bar intents.
                    reserved_cash = self._release_lost_intents(
                        immediate_intents, immediate_fills, immediate_carry_forward, reserved_cash
                    )
                    cash, reserved_cash = self._apply_fills(
                        run_id=run_id,
                        timestamp=timestamp,
                        fills=immediate_fills,
                        positions=positions,
                        cash=cash,
                        reserved_cash=reserved_cash,
                        prices=prices,
                        realized_pnl_by_symbol=realized_pnl_by_symbol,
                    )
                    # Carry-forward intents retain their reservation — no re-reserve.
                    if immediate_carry_forward:
                        scheduled.setdefault(bar_index + 1, []).extend(immediate_carry_forward)

                # Reserve and schedule future child slices (offset > 0).
                for off, ci in child_pairs:
                    if off > 0:
                        cash, reserved_cash, accepted = self._try_reserve(ci, cash, reserved_cash)
                        if accepted:
                            scheduled.setdefault(bar_index + off, []).append(ci)
            else:
                # All children deferred: reserve when scheduling.
                for off, ci in child_pairs:
                    target = bar_index + latency_bars + off
                    cash, reserved_cash, accepted = self._try_reserve(ci, cash, reserved_cash)
                    if accepted:
                        scheduled.setdefault(target, []).append(ci)

            fills = deferred_fills + immediate_fills

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
                    reserved_cash=reserved_cash,
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

        # Release reservations for all orders that remained pending at simulation end.
        # These would never fill (no more bars), so their reserved cash must be freed.
        for pending_intents in scheduled.values():
            for intent in pending_intents:
                released = self._estimate_reservation(intent)
                if released > Decimal("0"):
                    reserved_cash = max(Decimal("0"), reserved_cash - released)
                    logger.debug(
                        "reserved_cash.released_on_simulation_end",
                        extra={
                            "intent_id": str(getattr(intent, "intent_id", "?")),
                            "symbol": str(getattr(intent, "symbol", "?")),
                            "released": str(released),
                        },
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
            signal_log=pd.DataFrame(signal_rows)
            if signal_rows
            else pd.DataFrame(
                columns=[
                    "run_id",
                    "strategy_id",
                    "symbol",
                    "timestamp",
                    "direction",
                    "strength",
                    "signal_type",
                ]
            ),
        )

    def _plan_child_orders(
        self,
        *,
        order_intents: list[OrderIntent],
        execution_policy_config: ExecutionPolicyConfig | None,
        prices: dict[str, float],
    ) -> list[tuple[int, OrderIntent]]:
        """Apply execution policy to convert parent intents to (bar_offset, child_intent) pairs.

        When execution_policy_config is None the method returns [(0, intent)] per parent,
        preserving identical behaviour to the pre-policy simulation path.
        """
        pairs: list[tuple[int, OrderIntent]] = []
        for intent in order_intents:
            if execution_policy_config is None:
                pairs.append((0, intent))
            else:
                ref = prices.get(intent.symbol)
                ref_price = Decimal(str(ref)) if ref is not None else None
                pairs.extend(
                    self.simulated_execution_model.plan(
                        intent=intent,
                        config=execution_policy_config,
                        reference_price=ref_price,
                    )
                )
        return pairs

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
    ) -> list[OrderIntent]:
        targets = self.position_sizer.compute_targets(signals=signals, prices=prices)
        order_intents: list[OrderIntent] = []

        for symbol, target_qty in targets.items():
            current_qty = 0
            existing = positions.get(symbol)
            if existing is not None:
                current_qty = int(existing.quantity)

            delta = target_qty - current_qty
            if delta == 0:
                continue

            side = Side.BUY if delta > 0 else Side.SELL
            qty = abs(delta)
            price = prices.get(symbol)
            if price is None:
                continue

            # Deterministic intent_id (P-01): stable given identical run_id, symbol,
            # timestamp, side, and delta. Carry-forward copies keep this intent_id so
            # fill lineage is traceable across bars.
            _intent_key = (
                f"{run_id}:{strategy_id}:{symbol}:{timestamp.isoformat()}:{side.value}:{delta}"
            )
            order_intents.append(
                OrderIntent(
                    intent_id=uuid5(_INTENT_NS, _intent_key),
                    idempotency_key=f"{run_id}-{strategy_id}-{symbol}-{timestamp.isoformat()}",
                    run_id=run_id,
                    strategy_id=strategy_id,
                    timestamp=timestamp,
                    bar_timestamp=timestamp,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    notional=None,
                    order_type=OrderType.MARKET,
                    limit_price=Decimal(str(price)),
                    stop_price=None,
                    time_in_force=TimeInForce.DAY,
                    extended_hours=False,
                    client_order_id=f"{strategy_id}-{symbol}-{timestamp.isoformat()}-{side.value}",
                    metadata=None,
                )
            )

        return order_intents

    def _simulate_fills(
        self,
        *,
        order_intents: list[Any],
        bars_at_timestamp: dict[str, Any],
        simulated_execution_service: Any,
    ) -> tuple[list[Any], list[Any]]:
        batch = simulated_execution_service.fill(
            order_intents=order_intents,
            bars_at_timestamp=bars_at_timestamp,
        )
        return batch.fills, batch.carry_forward

    @staticmethod
    def _estimate_reservation(intent: Any) -> Decimal:
        """Estimate the cash notional to reserve for a pending buy order.

        Uses intent.limit_price as the reference price (market orders set limit_price
        to the current bar close when constructed; child intents inherit or override it).
        Sell orders return zero — no reservation is needed for the sell side.
        """
        side = getattr(intent, "side", None)
        side_val = getattr(side, "value", str(side)).lower() if side is not None else ""
        if side_val != "buy":
            return Decimal("0")
        qty = intent.qty if intent.qty is not None else Decimal("0")
        limit_price = getattr(intent, "limit_price", None)
        price = limit_price if limit_price is not None else Decimal("0")
        return Decimal(str(qty)) * Decimal(str(price))

    def _try_reserve(
        self,
        intent: Any,
        cash: Decimal,
        reserved_cash: Decimal,
    ) -> tuple[Decimal, Decimal, bool]:
        """Attempt to reserve cash for a buy order.

        Returns (cash, reserved_cash, accepted).  cash is unchanged; reserved_cash
        increases by the estimated notional on success.  On insufficient buying power
        the intent is rejected (accepted=False) and reserved_cash is unchanged.
        Sell orders are always accepted with no reservation.
        """
        estimated = self._estimate_reservation(intent)
        if estimated == Decimal("0"):
            return cash, reserved_cash, True

        buying_power = cash - reserved_cash
        if estimated > buying_power:
            logger.warning(
                "reserved_cash.insufficient_buying_power",
                extra={
                    "intent_id": str(getattr(intent, "intent_id", "?")),
                    "symbol": str(getattr(intent, "symbol", "?")),
                    "estimated_notional": str(estimated),
                    "available_buying_power": str(buying_power),
                    "reserved_cash": str(reserved_cash),
                },
            )
            return cash, reserved_cash, False

        new_reserved = reserved_cash + estimated
        logger.debug(
            "reserved_cash.reserved_for_order",
            extra={
                "intent_id": str(getattr(intent, "intent_id", "?")),
                "symbol": str(getattr(intent, "symbol", "?")),
                "reserved_amount": str(estimated),
                "total_reserved_cash": str(new_reserved),
                "buying_power_after": str(cash - new_reserved),
            },
        )
        return cash, new_reserved, True

    def _release_lost_intents(
        self,
        intents: list[Any],
        fills: list[Any],
        carry_forward: list[Any],
        reserved_cash: Decimal,
    ) -> Decimal:
        """Release reservation for intents that produced neither a fill nor a carry-forward.

        These are orders that were stochastically rejected or had no bar data.
        Their upfront reservation must be freed so buying power is restored.
        """
        covered = {getattr(f, "intent_id", None) for f in fills} | {
            getattr(cf, "intent_id", None) for cf in carry_forward
        }
        updated = reserved_cash
        for intent in intents:
            if getattr(intent, "intent_id", None) not in covered:
                released = self._estimate_reservation(intent)
                if released > Decimal("0"):
                    updated = max(Decimal("0"), updated - released)
                    logger.debug(
                        "reserved_cash.released_on_lost_intent",
                        extra={
                            "intent_id": str(getattr(intent, "intent_id", "?")),
                            "symbol": str(getattr(intent, "symbol", "?")),
                            "released": str(released),
                            "remaining_reserved": str(updated),
                        },
                    )
        return updated

    def _apply_fills(
        self,
        *,
        run_id: UUID,
        timestamp: datetime,
        fills: list[Any],
        positions: dict[str, Position],
        cash: Decimal,
        reserved_cash: Decimal,
        prices: dict[str, float],
        realized_pnl_by_symbol: dict[str, Decimal],
    ) -> tuple[Decimal, Decimal]:
        updated_cash = cash
        updated_reserved = reserved_cash

        for fill in fills:
            cash_snapshot = CashSnapshot(
                snapshot_id=uuid4(),
                run_id=run_id,
                timestamp=timestamp,
                currency="USD",
                cash=updated_cash,
                buying_power=updated_cash - updated_reserved,
                reserved_cash=updated_reserved,
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
            updated_reserved = cash_result.reserved_cash

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

        return updated_cash, updated_reserved

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
        return cash + self._calculate_positions_value(positions=positions, prices=prices)

    def _build_equity_row(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
        cash: Decimal,
        reserved_cash: Decimal,
        positions: dict[str, Position],
        prices: dict[str, float],
    ) -> dict[str, Any]:
        positions_value = self._calculate_positions_value(positions=positions, prices=prices)
        equity = cash + positions_value
        buying_power = cash - reserved_cash

        return {
            "run_id": str(run_id),
            "strategy_id": strategy_id,
            "timestamp": timestamp,
            "equity": float(equity),
            "cash": float(cash),
            "reserved_cash": float(reserved_cash),
            "buying_power": float(buying_power),
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

    def _build_signal_rows(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        timestamp: datetime,
        signals: list[Signal],
    ) -> list[dict[str, Any]]:
        rows = []
        for signal in signals:
            rows.append(
                {
                    "run_id": str(run_id),
                    "strategy_id": strategy_id,
                    "symbol": signal.symbol,
                    "timestamp": timestamp,
                    "direction": signal.direction.value,
                    "signal_type": signal.signal_type if hasattr(signal, "signal_type") else None,
                }
            )
        return rows
