from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill

logger = logging.getLogger(__name__)


class StubRiskStateReader:
    def get_gross_exposure(self) -> float:
        return 0.0

    def get_symbol_exposure(self, symbol: str) -> float:
        return 0.0

    def get_daily_notional_traded(self, trade_date: date) -> float:
        return 0.0

    def get_reserved_cash(self) -> float:
        return 0.0

    def get_position_qty(self, symbol: str) -> float:
        return 0.0


class PositionAwareRiskStateReader(StubRiskStateReader):
    """Account-level risk state for the live pre-trade checks.

    Symbol exposure, position quantity and gross exposure come from the latest
    position snapshot (the ledger, which post-fill accounting keeps in step with the
    broker); daily notional traded is the sum of today's booked fills. Reserved cash
    keeps the stub's zero: the live path does not reserve cash at scheduling time.

    Orders submitted earlier in the same tick are not in any snapshot yet, so the
    submission job reports each accepted order through ``record_submitted_order`` and
    the reader adds it to what it answers for the rest of the tick. Without that, every
    intent in a bar was checked against the same opening state and a cap could be
    overshot by one bar's worth of orders.

    The ledger snapshot itself runs one tick behind the broker: a fill from tick N is
    booked by tick N+1's reconciliation step, which runs *after* tick N+1's risk check.
    So before the checks run, ``refresh_from_broker`` replaces the snapshot view with
    the broker's live positions (what the strategy evaluation already trades against).
    Soak day 2 (2026-10-22): GOOGL was checked against $62.8k because the IWM buy from
    the previous bar was not in the ledger yet, and gross exposure reached $112.5k on a
    $100k cap. If the broker cannot be read, the ledger view stays in force.
    """

    def __init__(
        self,
        portfolio_reader: PortfolioRiskStateReader,
        *,
        daily_notional_traded: Callable[[date], float] | None = None,
    ) -> None:
        self._portfolio_reader = portfolio_reader
        self._daily_notional_traded = daily_notional_traded
        self._pending_gross_delta = 0.0
        self._pending_symbol_delta: dict[str, float] = {}
        self._pending_daily_notional = 0.0

    @classmethod
    def from_session(cls, session: Session) -> PositionAwareRiskStateReader:
        return cls(
            PortfolioRiskStateReader.from_session(session),
            daily_notional_traded=lambda trade_date: fills_notional_on(session, trade_date),
        )

    def refresh_from_broker(self, broker_client: Any) -> bool:
        """Re-base positions on the broker's live holdings; return whether that worked.

        Keeps the ledger-derived equity and anything already recorded for this tick.
        A broker that cannot answer (no ``get_positions``, an error, an unparseable row)
        leaves the ledger view in place and is logged, never raised: the caps still
        apply, just against the state that is one tick old.
        """
        get_positions = getattr(broker_client, "get_positions", None)
        if get_positions is None:
            return False
        try:
            rows = list(get_positions())
            positions = [_broker_row_to_position(row) for row in rows]
        except Exception:
            logger.warning(
                "risk_state_reader.broker_positions_unavailable",
                extra={"fallback": "ledger_snapshot"},
                exc_info=True,
            )
            return False
        self._portfolio_reader = PortfolioRiskStateReader.from_position_items(
            positions, total_equity=self._portfolio_reader.total_equity
        )
        return True

    def get_gross_exposure(self) -> float:
        base = float(self._portfolio_reader.get_gross_exposure_usd())
        return max(0.0, base + self._pending_gross_delta)

    def get_symbol_exposure(self, symbol: str) -> float:
        base = float(abs(self._portfolio_reader.get_symbol_exposure_usd(symbol)))
        return max(0.0, base + self._pending_symbol_delta.get(_normalize(symbol), 0.0))

    def get_daily_notional_traded(self, trade_date: date) -> float:
        base = self._daily_notional_traded(trade_date) if self._daily_notional_traded else 0.0
        return base + self._pending_daily_notional

    def get_position_qty(self, symbol: str) -> float:
        return float(self._portfolio_reader.get_symbol_quantity(symbol))

    def record_submitted_order(self, *, symbol: str, side: Side, notional: float) -> None:
        """Count an order the broker accepted this tick, before any snapshot shows it.

        Mirrors the pre-trade exposure arithmetic: a buy adds its notional; a sell first
        closes what is held (reducing exposure) and only the remainder adds.
        """
        key = _normalize(symbol)
        notional = abs(float(notional))
        if side == Side.BUY:
            delta = notional
        else:
            held = self.get_symbol_exposure(symbol)
            reduction = min(notional, held)
            delta = max(notional - reduction, 0.0) - reduction
        self._pending_symbol_delta[key] = self._pending_symbol_delta.get(key, 0.0) + delta
        self._pending_gross_delta += delta
        self._pending_daily_notional += notional


def fills_notional_on(session: Session, trade_date: date) -> float:
    """Sum of |quantity x price| over the fills booked on ``trade_date`` (UTC)."""
    start = datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    stmt = select(func.coalesce(func.sum(func.abs(Fill.quantity) * Fill.price), 0)).where(
        Fill.timestamp >= start,
        Fill.timestamp < end,
    )
    total = session.execute(stmt).scalar_one()
    return float(Decimal(str(total)))


def _broker_row_to_position(row: Mapping[str, Any]) -> Position:
    """Map one broker position payload (Alpaca shape) to the Position contract.

    ``market_value`` is what the exposure caps count; when the broker omits it, fall
    back to quantity x current price, then quantity x average entry price.
    """
    quantity = Decimal(str(row["qty"]))
    market_price = _optional_decimal(row.get("current_price"))
    avg_cost = _optional_decimal(row.get("avg_entry_price"))
    market_value = _optional_decimal(row.get("market_value"))
    if market_value is None:
        reference = market_price if market_price is not None else avg_cost
        market_value = quantity * reference if reference is not None else None
    return Position(
        symbol=str(row["symbol"]),
        quantity=quantity,
        avg_cost=avg_cost,
        market_price=market_price,
        market_value=market_value,
        unrealized_pnl=_optional_decimal(row.get("unrealized_pl")),
    )


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalize(symbol: str) -> str:
    return symbol.strip().upper()
