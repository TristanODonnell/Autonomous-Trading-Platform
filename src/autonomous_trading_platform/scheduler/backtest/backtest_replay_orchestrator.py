"""
Backtest Replay Orchestrator

Walks historical bar data, generates MA-crossover signals, writes synthetic
fills + position/cash snapshots to the SOR so Portfolio and Dashboard pages
show realistic data without live trading.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    LiquiditySide,
    OrderSource,
    Side,
)
from autonomous_trading_platform.scheduler.backtest.backtest_config import BacktestConfig
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.repositories.core.market_bar_repository import (
    MarketBarRepository,
)


@dataclass
class BacktestResult:
    run_id: str
    bars_processed: int
    fills_created: int
    snapshots_written: int
    final_equity: Decimal
    final_cash: Decimal


class _PositionTracker:
    """Tracks open positions and cash across the replay."""

    def __init__(self, initial_capital: Decimal) -> None:
        self.cash = initial_capital
        self.positions: dict[str, Decimal] = {}  # symbol -> quantity
        self.avg_costs: dict[str, Decimal] = {}  # symbol -> avg cost basis

    def buy(self, symbol: str, price: Decimal, qty: Decimal, fees: Decimal) -> bool:
        total_cost = price * qty + fees
        if total_cost > self.cash:
            return False
        prev_qty = self.positions.get(symbol, Decimal("0"))
        prev_cost = self.avg_costs.get(symbol, Decimal("0")) * prev_qty
        new_qty = prev_qty + qty
        self.avg_costs[symbol] = (prev_cost + price * qty) / new_qty
        self.positions[symbol] = new_qty
        self.cash -= total_cost
        return True

    def sell(self, symbol: str, price: Decimal, fees: Decimal) -> Decimal | None:
        qty = self.positions.pop(symbol, None)
        if qty is None:
            return None
        self.avg_costs.pop(symbol, None)
        proceeds = price * qty - fees
        self.cash += proceeds
        return qty

    def market_value(self, current_prices: dict[str, Decimal]) -> Decimal:
        total = self.cash
        for sym, qty in self.positions.items():
            price = current_prices.get(sym, Decimal("0"))
            total += qty * price
        return total


class _MASignal:
    """Per-symbol MA crossover signal tracker."""

    def __init__(self, short_window: int, long_window: int) -> None:
        self._short = short_window
        self._long = long_window
        self._prices: deque[Decimal] = deque(maxlen=long_window)
        self._prev_signal: int | None = None  # +1 buy, -1 sell, 0 flat

    def update(self, close: Decimal) -> int | None:
        """Feed a close price; returns +1 (buy cross), -1 (sell cross), or None (no change)."""
        self._prices.append(close)
        if len(self._prices) < self._long:
            return None

        short_ma = sum(list(self._prices)[-self._short :]) / self._short
        long_ma = sum(self._prices) / self._long

        signal = 1 if short_ma > long_ma else -1
        cross = None if signal == self._prev_signal else signal
        self._prev_signal = signal
        return cross


class BacktestReplayOrchestrator:
    def __init__(self, session: Session, config: BacktestConfig) -> None:
        self._session = session
        self._config = config

    def run(self, *, progress: bool = True) -> BacktestResult:
        cfg = self._config
        run_id = uuid.uuid4()

        start_ts = datetime(
            cfg.start_date.year, cfg.start_date.month, cfg.start_date.day, tzinfo=UTC
        )
        end_ts = datetime(
            cfg.end_date.year, cfg.end_date.month, cfg.end_date.day, 23, 59, 59, tzinfo=UTC
        )

        repo = MarketBarRepository(self._session)
        bars = repo.get_bars_for_symbols_between(
            symbols=cfg.symbols,
            start_ts=start_ts,
            end_ts=end_ts,
        )

        if not bars:
            if progress:
                print(
                    f"  No bars found for {cfg.symbols} between {cfg.start_date} and {cfg.end_date}"
                )
            return BacktestResult(
                run_id=str(run_id),
                bars_processed=0,
                fills_created=0,
                snapshots_written=0,
                final_equity=cfg.initial_capital,
                final_cash=cfg.initial_capital,
            )

        # Group bars by timestamp
        bars_by_ts: dict[datetime, dict[str, Decimal]] = defaultdict(dict)
        for bar in bars:
            bars_by_ts[bar.timestamp][bar.symbol] = Decimal(str(bar.close))

        sorted_timestamps = sorted(bars_by_ts.keys())

        tracker = _PositionTracker(cfg.initial_capital)
        signals: dict[str, _MASignal] = {
            sym: _MASignal(cfg.short_window, cfg.long_window) for sym in cfg.symbols
        }

        fills_created = 0
        snapshots_written = 0
        bars_processed = 0
        current_day = None

        per_symbol_capital = cfg.initial_capital / len(cfg.symbols)

        for ts in sorted_timestamps:
            close_prices = bars_by_ts[ts]

            for symbol in cfg.symbols:
                close = close_prices.get(symbol)
                if close is None:
                    continue

                cross = signals[symbol].update(close)
                if cross is None:
                    continue

                if cross == 1 and symbol not in tracker.positions:
                    # BUY: allocate per-symbol capital
                    fill_price = close * (1 + cfg.slippage_rate)
                    qty = (per_symbol_capital / fill_price).to_integral_value(rounding=ROUND_DOWN)
                    if qty <= 0:
                        continue
                    fees = fill_price * qty * cfg.fee_rate
                    ok = tracker.buy(symbol, fill_price, qty, fees)
                    if ok:
                        self._write_fill(
                            run_id=run_id,
                            ts=ts,
                            symbol=symbol,
                            side=Side.BUY,
                            qty=qty,
                            price=fill_price,
                            fees=fees,
                        )
                        fills_created += 1

                elif cross == -1 and symbol in tracker.positions:
                    # SELL: exit full position
                    fill_price = close * (1 - cfg.slippage_rate)
                    qty = tracker.positions[symbol]
                    fees = fill_price * qty * cfg.fee_rate
                    tracker.sell(symbol, fill_price, fees)
                    self._write_fill(
                        run_id=run_id,
                        ts=ts,
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        price=fill_price,
                        fees=fees,
                    )
                    fills_created += 1

            # Write snapshot at end of each trading day
            day = ts.date()
            if current_day != day:
                if current_day is not None:
                    self._write_snapshots(
                        run_id=run_id,
                        ts=ts,
                        tracker=tracker,
                        current_prices=close_prices,
                    )
                    snapshots_written += 1
                    if progress:
                        equity = tracker.market_value(close_prices)
                        print(
                            f"  {current_day}  equity=${float(equity):>12,.2f}  "
                            f"cash=${float(tracker.cash):>12,.2f}  "
                            f"positions={len(tracker.positions)}  "
                            f"fills={fills_created}"
                        )
                current_day = day

            bars_processed += 1

        # Write final snapshot
        if current_day is not None and sorted_timestamps:
            last_prices = bars_by_ts[sorted_timestamps[-1]]
            self._write_snapshots(
                run_id=run_id,
                ts=sorted_timestamps[-1],
                tracker=tracker,
                current_prices=last_prices,
            )
            snapshots_written += 1

        self._session.commit()

        final_prices = bars_by_ts[sorted_timestamps[-1]] if sorted_timestamps else {}
        final_equity = tracker.market_value(final_prices)

        return BacktestResult(
            run_id=str(run_id),
            bars_processed=bars_processed,
            fills_created=fills_created,
            snapshots_written=snapshots_written,
            final_equity=final_equity,
            final_cash=tracker.cash,
        )

    def _write_fill(
        self,
        *,
        run_id: uuid.UUID,
        ts: datetime,
        symbol: str,
        side: Side,
        qty: Decimal,
        price: Decimal,
        fees: Decimal,
    ) -> None:
        fill_id = f"bt_{run_id}_{symbol}_{side.value}_{ts.isoformat()}"
        fill = Fill(
            fill_id=fill_id[:64],
            broker_order_id=fill_id[:64],
            intent_id=uuid.uuid4(),
            run_id=run_id,
            timestamp=ts,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            fees=fees,
            liquidity=LiquiditySide.TAKER,
            venue="backtest",
            meta={"source": "backtest_replay", "strategy_id": self._config.strategy_id},
        )
        self._session.add(fill)

    def _write_snapshots(
        self,
        *,
        run_id: uuid.UUID,
        ts: datetime,
        tracker: _PositionTracker,
        current_prices: dict[str, Decimal],
    ) -> None:
        pos_snapshot = PositionSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=run_id,
            timestamp=ts,
            source=OrderSource.SIMULATION,
        )
        self._session.add(pos_snapshot)
        self._session.flush()  # populate snapshot_id before creating items

        for symbol, qty in tracker.positions.items():
            price = current_prices.get(symbol, Decimal("0"))
            avg_cost = tracker.avg_costs.get(symbol, Decimal("0"))
            market_value = qty * price
            unrealized_pnl = market_value - avg_cost * qty
            item = PositionSnapshotItem(
                snapshot_id=pos_snapshot.snapshot_id,
                symbol=symbol,
                quantity=qty,
                avg_cost=avg_cost,
                market_price=price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
            )
            self._session.add(item)

        equity = tracker.market_value(current_prices)
        cash_snapshot = CashSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=run_id,
            timestamp=ts,
            currency="USD",
            cash=tracker.cash,
            buying_power=tracker.cash,
            reserved_cash=Decimal("0"),
            equity=equity,
            source=OrderSource.SIMULATION,
            capital_bucket=self._config.initial_capital,
        )
        self._session.add(cash_snapshot)
