"""Simulated broker client for platform backtest replay.

Drop-in replacement for AlpacaBrokerClient. Fills orders immediately against
the current tick's OHLCV bar loaded from Parquet, using the same
SimulatedExecutionService as the research simulation pipeline.
Returns Alpaca-format response dicts so the existing order pipeline is unchanged.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    RAW_BARS_DATASET,
)


@dataclasses.dataclass
class _BarProxy:
    """Minimal bar object compatible with SimulatedExecutionService.fill()."""

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: datetime


class _IntentProxy:
    """Duck-typed OrderIntent for SimulatedExecutionService.fill().

    Wraps the Alpaca payload dict produced by AlpacaBrokerAdapter.to_payload()
    and exposes the attributes expected by the fill engine.
    """

    __slots__ = (
        "symbol",
        "qty",
        "order_type",
        "side",
        "limit_price",
        "run_id",
        "intent_id",
    )

    def __init__(self, payload: dict[str, Any], broker_order_id: str) -> None:
        self.symbol: str = payload["symbol"]
        qty_raw = payload.get("qty")
        self.qty: Decimal | None = Decimal(str(qty_raw)) if qty_raw is not None else None
        # Alpaca "type" field = "market" or "limit"
        self.order_type: str = str(payload.get("type", "market")).lower()
        self.side: str = str(payload.get("side", "buy")).lower()
        limit_raw = payload.get("limit_price")
        self.limit_price: Decimal | None = (
            Decimal(str(limit_raw)) if limit_raw is not None else None
        )
        # Used by SimulatedExecutionService for fill-ID generation.
        self.run_id: str = str(uuid4())
        self.intent_id: str = broker_order_id


class SimulatedBrokerClient:
    """Simulated broker for platform backtest replay.

    Replaces AlpacaBrokerClient when running historical simulations.
    Orders are filled synchronously against the current tick's bar on submit;
    the reconciliation job finds no open orders and is effectively a no-op.
    """

    def __init__(
        self,
        *,
        session: Any,
        timestamp: datetime,
        simulated_execution_service: SimulatedExecutionService,
        starting_cash: Decimal = Decimal("100000"),
        base_path: str = "data",
        dataset_version_id: str | None = None,
    ) -> None:
        self._session = session
        self._timestamp = timestamp
        self._execution_service = simulated_execution_service
        self._cash = starting_cash
        self._base_path = base_path
        self._dataset_version_id = dataset_version_id
        # broker_order_id → Alpaca-format order dict
        self._orders: dict[str, dict[str, Any]] = {}
        # client_order_id → broker_order_id
        self._by_client_order_id: dict[str, str] = {}
        # per-tick bar cache to avoid redundant Parquet reads
        self._bar_cache: dict[str, _BarProxy | None] = {}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker_order_id = str(uuid4())
        now_iso = self._timestamp.isoformat()
        symbol = payload["symbol"]
        client_order_id = str(payload.get("client_order_id", ""))

        base: dict[str, Any] = {
            "id": broker_order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": payload["side"],
            "type": payload["type"],
            "time_in_force": payload["time_in_force"],
            "extended_hours": payload.get("extended_hours", False),
            "qty": payload.get("qty"),
            "notional": payload.get("notional"),
            "limit_price": payload.get("limit_price"),
            "stop_price": payload.get("stop_price"),
            "submitted_at": now_iso,
            "updated_at": now_iso,
            "filled_qty": "0",
            "filled_avg_price": None,
        }

        bar = self._load_bar(symbol)

        if bar is not None and payload.get("qty"):
            intent = _IntentProxy(payload, broker_order_id)
            batch = self._execution_service.fill(
                order_intents=[intent],
                bars_at_timestamp={symbol: bar},
            )
            if batch.fills:
                fill = batch.fills[0]
                order: dict[str, Any] = {
                    **base,
                    "status": "filled",
                    "filled_qty": str(fill.quantity),
                    "filled_avg_price": str(fill.price),
                    "filled_at": now_iso,
                }
                self._orders[broker_order_id] = order
                self._by_client_order_id[client_order_id] = broker_order_id
                return order

        # Bar unavailable or qty missing — leave as open/pending.
        order = {**base, "status": "new"}
        self._orders[broker_order_id] = order
        self._by_client_order_id[client_order_id] = broker_order_id
        return order

    def get_order_by_id(self, order_id: str) -> dict[str, Any]:
        return self._orders.get(order_id, {"id": order_id, "status": "not_found"})

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        broker_id = self._by_client_order_id.get(client_order_id)
        if broker_id is None:
            return None
        return self._orders.get(broker_id)

    def list_open_orders(self) -> list[dict[str, Any]]:
        terminal = {"filled", "canceled", "expired", "rejected", "not_found"}
        return [o for o in self._orders.values() if o.get("status") not in terminal]

    def cancel_order(self, order_id: str) -> None:
        if order_id in self._orders:
            self._orders[order_id]["status"] = "canceled"

    # ------------------------------------------------------------------
    # Account & Positions
    # ------------------------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        return {
            "equity": str(self._cash),
            "cash": str(self._cash),
            "buying_power": str(self._cash),
            "portfolio_value": str(self._cash),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        return []

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        return None

    # ------------------------------------------------------------------
    # Market Data (used by ExecutionPolicyEngine for LIMIT/VWAP modes)
    # ------------------------------------------------------------------

    def get_latest_quotes(self, symbols: list[str]) -> dict[str, Any]:
        quotes: dict[str, Any] = {}
        for symbol in symbols:
            bar = self._load_bar(symbol)
            if bar is not None:
                quotes[symbol] = {
                    "ap": float(bar.close),
                    "bp": float(bar.close),
                    "as_": 100,
                    "bs": 100,
                    "t": self._timestamp.isoformat(),
                }
        return quotes

    def get_latest_trades(self, symbols: list[str]) -> dict[str, Any]:
        trades: dict[str, Any] = {}
        for symbol in symbols:
            bar = self._load_bar(symbol)
            if bar is not None:
                trades[symbol] = {
                    "p": float(bar.close),
                    "s": 100,
                    "t": self._timestamp.isoformat(),
                }
        return trades

    # ------------------------------------------------------------------
    # Intraday replay helpers
    # ------------------------------------------------------------------

    def advance_to(self, timestamp: datetime) -> None:
        """Advance the broker client to a new bar timestamp.

        Called between intraday ticks so the same client instance is reused
        across all 5-min bars within a trading day. Clears the bar cache so
        each tick reads the bar for its own timestamp.
        """
        self._timestamp = timestamp
        self._bar_cache.clear()

    @property
    def orders_submitted_count(self) -> int:
        return len(self._orders)

    @property
    def fills_count(self) -> int:
        return sum(1 for o in self._orders.values() if o.get("status") == "filled")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal bar loading
    # ------------------------------------------------------------------

    def _load_bar(self, symbol: str) -> _BarProxy | None:
        if symbol in self._bar_cache:
            return self._bar_cache[symbol]
        bar = self._fetch_bar_from_parquet(symbol)
        self._bar_cache[symbol] = bar
        return bar

    def _fetch_bar_from_parquet(self, symbol: str) -> _BarProxy | None:
        """Load the latest validated bar for this tick's date from Parquet.

        When dataset_version_id is set (backtest mode), reads directly from
        that dataset. Otherwise queries the SOR for the best validated dataset.
        Returns None if no bar is available, causing the order to stay pending.
        """
        from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
        from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

        tick_date = self._timestamp.date()
        reader = HistoricalBarDatasetReader(session=self._session, base_path=self._base_path)

        if self._dataset_version_id is not None:
            try:
                table = reader.read_with_pyarrow(
                    dataset=RAW_BARS_DATASET,
                    dataset_version=self._dataset_version_id,
                    symbol=symbol,
                    start_date=tick_date,
                    end_date=tick_date,
                )
                if table.num_rows > 0:
                    d = table.to_pydict()
                    idx = table.num_rows - 1
                    return _BarProxy(
                        open=Decimal(str(d["open"][idx])),
                        high=Decimal(str(d["high"][idx])),
                        low=Decimal(str(d["low"][idx])),
                        close=Decimal(str(d["close"][idx])),
                        volume=Decimal(str(d["volume"][idx])),
                        timestamp=self._timestamp,
                    )
            except Exception:
                pass
            return None

        for dataset_name, dataset_obj in (
            ("adjusted_bars", ADJUSTED_BARS_DATASET),
            ("raw_bars", RAW_BARS_DATASET),
        ):
            row = (
                self._session.query(DatasetVersions)
                .filter(DatasetVersions.dataset_name == dataset_name)
                .filter(DatasetVersions.validation_status == "validated")
                .filter(DatasetVersions.date_coverage_start <= tick_date)
                .filter(DatasetVersions.date_coverage_end >= tick_date)
                .order_by(DatasetVersions.created_at.desc())
                .first()
            )
            if row is None:
                continue

            try:
                table = reader.read_with_pyarrow(
                    dataset=dataset_obj,
                    dataset_version=row.dataset_version_id,
                    symbol=symbol,
                    start_date=tick_date,
                    end_date=tick_date,
                )
                if table.num_rows == 0:
                    continue

                d = table.to_pydict()
                idx = table.num_rows - 1  # last bar of the day
                return _BarProxy(
                    open=Decimal(str(d["open"][idx])),
                    high=Decimal(str(d["high"][idx])),
                    low=Decimal(str(d["low"][idx])),
                    close=Decimal(str(d["close"][idx])),
                    volume=Decimal(str(d["volume"][idx])),
                    timestamp=self._timestamp,
                )
            except Exception:
                continue

        return None
