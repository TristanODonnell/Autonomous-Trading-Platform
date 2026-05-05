"""
Computes win rate, avg win/loss and profit factor from simulation trade logs.

trade_logs columns: run_id, strategy_id, symbol, timestamp, side, quantity,
                    price, notional, fees, slippage

Each row is a *fill*, not a round-trip. Win/loss is determined by FIFO-matching
BUY fills against SELL fills per symbol.
    round_trip_pnl = (sell_price - buy_price) * matched_qty - apportioned_fees
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

_INFINITE_PROFIT_FACTOR_CAP: float = 99.0
"""
Sentinel returned by TradeMetrics.safe_profit_factor when profit_factor is inf
(i.e. no losing trades).  99.0 is recognisable as a cap rather than a real
value — no realistic strategy produces a natural profit factor at this level.
Use safe_profit_factor in any aggregation (mean, ranking, composite scoring)
to avoid silently propagating inf through statistics.mean() or numpy operations.
Use profit_factor directly when you need to distinguish "zero losses" from
"very high but finite profit factor".
"""


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    total_trades: int  # closed round-trip count
    win_rate: float  # wins / total_trades
    avg_win: float  # mean pnl of winning trades (positive $)
    avg_loss: float  # mean pnl of losing trades (negative $)
    profit_factor: float  # gross_profit / abs(gross_loss); inf if no losses
    largest_win: float  # best single trade pnl ($)
    largest_loss: float  # worst single trade pnl ($)

    @property
    def safe_profit_factor(self) -> float:
        """profit_factor capped at 99.0 for use in aggregations and scoring.

        Use this instead of profit_factor whenever the value will be passed to
        statistics.mean(), numpy operations, or composite scoring — inf
        silently corrupts those results.  The raw profit_factor field is
        preserved for callers that need the semantically exact value.
        """
        return min(self.profit_factor, _INFINITE_PROFIT_FACTOR_CAP)


_REQUIRED = {"symbol", "side", "quantity", "price", "fees", "timestamp"}


def _validate(trade_logs: pd.DataFrame) -> None:
    if trade_logs is None or trade_logs.empty:
        raise ValueError("trade_logs is empty.")
    missing = _REQUIRED - set(trade_logs.columns)
    if missing:
        raise ValueError(f"trade_logs missing columns: {missing}")


class _Lot(NamedTuple):
    quantity: float
    price: float
    fees: float


def _match_fifo(symbol_df: pd.DataFrame) -> list[float]:
    """
    FIFO-match buys against sells for one symbol.
    Fees are apportioned proportionally when a lot is partially closed.
    Returns a list of round-trip PnL values.
    """
    buy_queue: deque[_Lot] = deque()
    pnls: list[float] = []

    for _, row in symbol_df.sort_values("timestamp").iterrows():
        side = str(row["side"]).lower()
        qty = float(row["quantity"])
        price = float(row["price"])
        fees = float(row["fees"])

        if side == "buy":
            buy_queue.append(_Lot(qty, price, fees))

        elif side == "sell":
            remaining = qty
            while remaining > 0 and buy_queue:
                lot = buy_queue[0]
                matched = min(lot.quantity, remaining)
                buy_fee = lot.fees * (matched / lot.quantity)
                sell_fee = fees * (matched / qty)
                pnls.append((price - lot.price) * matched - buy_fee - sell_fee)
                remaining -= matched
                if matched >= lot.quantity:
                    buy_queue.popleft()
                else:
                    leftover = lot.quantity - matched
                    buy_queue[0] = _Lot(leftover, lot.price, lot.fees * (leftover / lot.quantity))

    return pnls


def _all_pnls(trade_logs: pd.DataFrame) -> list[float]:
    pnls: list[float] = []
    for _, group in trade_logs.groupby("symbol"):
        pnls.extend(_match_fifo(group))
    return pnls


_EMPTY = TradeMetrics(
    total_trades=0,
    win_rate=0.0,
    avg_win=0.0,
    avg_loss=0.0,
    profit_factor=0.0,
    largest_win=0.0,
    largest_loss=0.0,
)


def closed_trades(trade_logs: pd.DataFrame) -> pd.DataFrame:
    """
    Return a DataFrame of closed round-trip trades (trade_index, pnl, is_win).
    Useful for stability_metrics and debugging. Returns empty df if no trades.
    """
    if trade_logs is None or trade_logs.empty:
        return pd.DataFrame(columns=["trade_index", "pnl", "is_win"])
    _validate(trade_logs)
    pnls = _all_pnls(trade_logs)
    if not pnls:
        return pd.DataFrame(columns=["trade_index", "pnl", "is_win"])
    return pd.DataFrame(
        {"trade_index": range(len(pnls)), "pnl": pnls, "is_win": [p > 0 for p in pnls]}
    )


def win_rate(trade_logs: pd.DataFrame) -> float:
    """wins / total_closed_trades. Returns 0.0 if no closed trades."""
    _validate(trade_logs)
    pnls = _all_pnls(trade_logs)
    return sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0


def avg_win(trade_logs: pd.DataFrame) -> float:
    """Mean pnl of winning trades (positive $). Returns 0.0 if no wins."""
    _validate(trade_logs)
    wins = [p for p in _all_pnls(trade_logs) if p > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trade_logs: pd.DataFrame) -> float:
    """Mean pnl of losing trades (negative $). Returns 0.0 if no losses."""
    _validate(trade_logs)
    losses = [p for p in _all_pnls(trade_logs) if p < 0]
    return sum(losses) / len(losses) if losses else 0.0


def profit_factor(trade_logs: pd.DataFrame) -> float:
    """
    gross_profit / abs(gross_loss).
    Returns inf if no losses but profit > 0; 0.0 if no trades or no profit.
    """
    _validate(trade_logs)
    pnls = _all_pnls(trade_logs)
    if not pnls:
        return 0.0
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl == 0.0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def trade_metrics(trade_logs: pd.DataFrame) -> TradeMetrics:
    """Compute all trade metrics in one call."""
    if trade_logs is None or trade_logs.empty:
        return _EMPTY
    _validate(trade_logs)
    pnls = _all_pnls(trade_logs)
    if not pnls:
        return _EMPTY
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return TradeMetrics(
        total_trades=len(pnls),
        win_rate=len(wins) / len(pnls),
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        profit_factor=(float("inf") if gl == 0 and gp > 0 else (0.0 if gl == 0 else gp / gl)),
        largest_win=max(pnls),
        largest_loss=min(pnls),
    )
