"""
BacktestBrokerClient

Drop-in replacement for AlpacaBrokerClient during backtest replay.
Sources positions, prices, and equity from the in-memory position tracker
rather than calling Alpaca.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.scheduler.backtest.backtest_config import BacktestConfig


class BacktestBrokerClient:
    """
    Mimics the AlpacaBrokerClient interface using in-memory state.

    Keeps positions/cash in sync via buy() / sell() calls from the orchestrator.
    Prices are refreshed each bar via set_current_prices().
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._cash = Decimal(str(config.initial_capital))
        self._positions: dict[str, Decimal] = {}  # symbol -> quantity
        self._avg_costs: dict[str, Decimal] = {}  # symbol -> avg cost
        self._current_prices: dict[str, Decimal] = {}
        self._pending_orders: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Price / state refresh (called by orchestrator each bar)
    # ------------------------------------------------------------------

    def set_current_prices(self, prices: dict[str, Decimal]) -> None:
        self._current_prices = prices

    def apply_fill(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        fees: Decimal,
    ) -> None:
        if side == "buy":
            total_cost = price * qty + fees
            prev_qty = self._positions.get(symbol, Decimal("0"))
            prev_cost = self._avg_costs.get(symbol, Decimal("0")) * prev_qty
            new_qty = prev_qty + qty
            if new_qty > 0:
                self._avg_costs[symbol] = (prev_cost + price * qty) / new_qty
            self._positions[symbol] = new_qty
            self._cash -= total_cost
        else:
            closed_qty = self._positions.pop(symbol, qty)
            self._avg_costs.pop(symbol, None)
            self._cash += price * closed_qty - fees

    # ------------------------------------------------------------------
    # AlpacaBrokerClient interface — account & positions
    # ------------------------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        equity = self._portfolio_equity()
        return {
            "equity": str(equity),
            "cash": str(self._cash),
            "buying_power": str(self._cash),
            "portfolio_value": str(equity),
            "status": "ACTIVE",
        }

    def get_positions(self) -> list[dict[str, Any]]:
        result = []
        for symbol, qty in self._positions.items():
            if qty <= 0:
                continue
            price = self._current_prices.get(symbol, Decimal("0"))
            avg_cost = self._avg_costs.get(symbol, Decimal("0"))
            market_value = qty * price
            unrealized_pl = market_value - avg_cost * qty
            result.append(
                {
                    "symbol": symbol,
                    "qty": str(qty),
                    "avg_entry_price": str(avg_cost),
                    "market_value": str(market_value),
                    "unrealized_pl": str(unrealized_pl),
                    "current_price": str(price),
                }
            )
        return result

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        qty = self._positions.get(symbol)
        if qty is None or qty <= 0:
            return None
        price = self._current_prices.get(symbol, Decimal("0"))
        avg_cost = self._avg_costs.get(symbol, Decimal("0"))
        market_value = qty * price
        unrealized_pl = market_value - avg_cost * qty
        return {
            "symbol": symbol,
            "qty": str(qty),
            "avg_entry_price": str(avg_cost),
            "market_value": str(market_value),
            "unrealized_pl": str(unrealized_pl),
            "current_price": str(price),
        }

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_latest_trades(self, symbols: list[str]) -> dict[str, Any]:
        return {
            sym: {
                "p": float(self._current_prices.get(sym, Decimal("0"))),
                "s": 100,
                "t": datetime.now(UTC).isoformat(),
            }
            for sym in symbols
        }

    def get_latest_quotes(self, symbols: list[str]) -> dict[str, Any]:
        return {
            sym: {
                "ap": float(self._current_prices.get(sym, Decimal("0"))),
                "bp": float(self._current_prices.get(sym, Decimal("0"))),
                "as": 100,
                "bs": 100,
                "t": datetime.now(UTC).isoformat(),
            }
            for sym in symbols
        }

    # ------------------------------------------------------------------
    # Orders — synthetic fills, no Alpaca call
    # ------------------------------------------------------------------

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        order_id = f"bt_{uuid.uuid4().hex}"
        symbol = payload.get("symbol", "")
        price = float(self._current_prices.get(symbol, Decimal("0")))
        qty = payload.get("qty", "0")
        self._pending_orders[order_id] = {
            "id": order_id,
            "status": "filled",
            "symbol": symbol,
            "qty": qty,
            "filled_qty": qty,
            "filled_avg_price": str(price),
            "side": payload.get("side", "buy"),
            "order_type": payload.get("type", "market"),
            "time_in_force": payload.get("time_in_force", "day"),
            "created_at": datetime.now(UTC).isoformat(),
        }
        return self._pending_orders[order_id]

    def get_order_by_id(self, order_id: str) -> dict[str, Any]:
        return self._pending_orders.get(
            order_id,
            {"id": order_id, "status": "filled"},
        )

    def list_open_orders(self) -> list[dict[str, Any]]:
        return []

    def cancel_order(self, order_id: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _portfolio_equity(self) -> Decimal:
        invested = sum(
            qty * self._current_prices.get(sym, Decimal("0"))
            for sym, qty in self._positions.items()
        )
        return self._cash + invested

    # ------------------------------------------------------------------
    # Expose state for snapshot writing
    # ------------------------------------------------------------------

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def positions(self) -> dict[str, Decimal]:
        return dict(self._positions)

    @property
    def avg_costs(self) -> dict[str, Decimal]:
        return dict(self._avg_costs)

    def portfolio_equity(self) -> Decimal:
        return self._portfolio_equity()
