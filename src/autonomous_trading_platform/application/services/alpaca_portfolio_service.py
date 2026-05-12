from __future__ import annotations

from decimal import Decimal, InvalidOperation

from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient


def _d(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(default)


class AlpacaPortfolioService:
    def __init__(self, *, client: AlpacaBrokerClient, initial_capital: float) -> None:
        self._client = client
        self._initial_capital = Decimal(str(initial_capital))

    def get_summary(self) -> dict:
        account = self._client.get_account()

        equity = _d(account.get("equity"))
        cash = _d(account.get("cash"))
        last_equity = _d(account.get("last_equity") or account.get("equity"))

        todays_pnl = equity - last_equity
        total_pnl = equity - self._initial_capital

        todays_pnl_pct = todays_pnl / last_equity if last_equity != Decimal("0") else Decimal("0")
        total_pnl_pct = (
            total_pnl / self._initial_capital
            if self._initial_capital != Decimal("0")
            else Decimal("0")
        )

        return {
            "current_portfolio_value": equity,
            "todays_pnl_amount": todays_pnl,
            "todays_pnl_percent": todays_pnl_pct,
            "total_pnl_amount": total_pnl,
            "total_pnl_percent": total_pnl_pct,
            "cash_balance": cash,
        }

    def get_holdings(self) -> dict:
        positions = self._client.get_positions()
        holdings = []
        for pos in positions:
            qty = _d(pos.get("qty"))
            if qty == Decimal("0"):
                continue
            holdings.append(
                {
                    "symbol": pos.get("symbol", ""),
                    "company_name": pos.get("symbol", ""),
                    "market_value": _d(pos.get("market_value")),
                    "quantity": qty,
                    "average_entry_price": _d(pos.get("avg_entry_price")),
                    "current_price": _d(pos.get("current_price")),
                    "todays_change_percent": _d(pos.get("unrealized_intraday_plpc")),
                    "todays_change_absolute": _d(pos.get("unrealized_intraday_pl")),
                    "strategy_id": "unknown",
                }
            )
        return {"holdings": holdings}
