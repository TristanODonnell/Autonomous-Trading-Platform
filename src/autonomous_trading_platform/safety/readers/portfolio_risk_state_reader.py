from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import asc, case, desc, select
from sqlalchemy.orm import Session, selectinload

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot

ZERO = Decimal("0")


class PortfolioRiskStateReader:
    """
    Queryable aggregate exposure state for portfolio-level pre-trade risk checks.

    The reader is intentionally stateless. Construct a fresh instance from the
    current PositionSnapshot/CashSnapshot before routing orders; those snapshots
    remain the source of truth for positions and equity.
    """

    def __init__(
        self,
        symbol_exposures_usd: dict[str, Decimal],
        total_equity: Decimal,
        *,
        symbol_quantities: dict[str, Decimal] | None = None,
    ) -> None:
        self._symbol_exposures = {
            self._normalize_symbol(symbol): Decimal(value)
            for symbol, value in symbol_exposures_usd.items()
            if Decimal(value) != ZERO
        }
        self._symbol_quantities = {
            self._normalize_symbol(symbol): Decimal(value)
            for symbol, value in (symbol_quantities or {}).items()
            if Decimal(value) != ZERO
        }
        self._total_equity = Decimal(total_equity)

    @classmethod
    def from_session(cls, session: Session) -> PortfolioRiskStateReader:
        position_snapshot = cls._latest_position_snapshot(session)
        cash_snapshot = cls._latest_cash_snapshot(session)
        total_equity = (
            Decimal(cash_snapshot.equity)
            if cash_snapshot is not None and cash_snapshot.equity is not None
            else ZERO
        )
        return cls.from_position_snapshot(position_snapshot, total_equity=total_equity)

    @classmethod
    def from_position_snapshot(
        cls,
        position_snapshot: Any | None,
        *,
        total_equity: Decimal,
    ) -> PortfolioRiskStateReader:
        positions = position_snapshot.positions if position_snapshot is not None else []
        return cls.from_position_items(positions, total_equity=total_equity)

    @classmethod
    def from_position_items(
        cls,
        positions: Iterable[Any],
        *,
        total_equity: Decimal,
    ) -> PortfolioRiskStateReader:
        exposures: dict[str, Decimal] = {}
        quantities: dict[str, Decimal] = {}

        for position in positions:
            symbol = cls._normalize_symbol(position.symbol)
            quantity = Decimal(position.quantity)
            market_value = cls._position_market_value(position)
            if quantity == ZERO and market_value == ZERO:
                continue
            exposures[symbol] = exposures.get(symbol, ZERO) + abs(market_value)
            quantities[symbol] = quantities.get(symbol, ZERO) + quantity

        return cls(exposures, total_equity, symbol_quantities=quantities)

    def get_symbol_exposure_usd(self, symbol: str) -> Decimal:
        return self._symbol_exposures.get(self._normalize_symbol(symbol), ZERO)

    def get_symbol_exposure_pct(
        self,
        symbol: str,
        total_equity: Decimal | None = None,
    ) -> Decimal:
        equity = self._total_equity if total_equity is None else Decimal(total_equity)
        if equity <= ZERO:
            return ZERO
        return self.get_symbol_exposure_usd(symbol) / equity

    def get_symbol_quantity(self, symbol: str) -> Decimal:
        return self._symbol_quantities.get(self._normalize_symbol(symbol), ZERO)

    def has_symbol_quantity(self, symbol: str) -> bool:
        return self._normalize_symbol(symbol) in self._symbol_quantities

    def get_all_symbol_exposures(self) -> dict[str, Decimal]:
        return dict(self._symbol_exposures)

    def get_gross_exposure_usd(self) -> Decimal:
        return sum(self._symbol_exposures.values(), ZERO)

    def get_net_exposure_usd(self) -> Decimal:
        return sum(
            (self._signed_symbol_exposure(symbol) for symbol in self._symbol_exposures), ZERO
        )

    def get_top_concentrated_symbols(self, n: int = 10) -> list[tuple[str, Decimal]]:
        """Return the top-n symbols by USD exposure, then symbol for deterministic ties."""
        return sorted(
            self._symbol_exposures.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[:n]

    def get_concentration_metrics(
        self,
        *,
        max_portfolio_symbol_pct: Decimal | None = None,
        top_n: int = 10,
    ) -> dict[str, object]:
        top_symbols = []
        for symbol, exposure_usd in self.get_top_concentrated_symbols(top_n):
            exposure_pct = self.get_symbol_exposure_pct(symbol)
            limit_utilization = (
                exposure_pct / max_portfolio_symbol_pct
                if max_portfolio_symbol_pct is not None and max_portfolio_symbol_pct > ZERO
                else None
            )
            top_symbols.append(
                {
                    "symbol": symbol,
                    "exposure_usd": exposure_usd,
                    "exposure_pct": float(exposure_pct),
                    "limit_utilization": (
                        float(limit_utilization) if limit_utilization is not None else None
                    ),
                }
            )

        return {
            "top_concentrated_symbols": top_symbols,
            "gross_exposure_usd": self.get_gross_exposure_usd(),
            "net_exposure_usd": self.get_net_exposure_usd(),
        }

    @property
    def total_equity(self) -> Decimal:
        return self._total_equity

    def _signed_symbol_exposure(self, symbol: str) -> Decimal:
        exposure = self.get_symbol_exposure_usd(symbol)
        quantity = self.get_symbol_quantity(symbol)
        if quantity < ZERO:
            return -exposure
        return exposure

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()

    @staticmethod
    def _position_market_value(position: Any) -> Decimal:
        if position.market_value is not None:
            return Decimal(position.market_value)
        if position.market_price is None:
            return ZERO
        return Decimal(position.quantity) * Decimal(position.market_price)

    @staticmethod
    def _latest_position_snapshot(session: Session) -> PositionSnapshot | None:
        stmt = (
            select(PositionSnapshot)
            .options(selectinload(PositionSnapshot.positions))
            .order_by(
                desc(PositionSnapshot.timestamp),
                asc(_position_source_priority()),
                desc(PositionSnapshot.snapshot_id),
            )
            .limit(1)
        )
        return cast(PositionSnapshot | None, session.scalars(stmt).one_or_none())

    @staticmethod
    def _latest_cash_snapshot(session: Session) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .order_by(
                desc(CashSnapshot.timestamp),
                asc(_cash_source_priority()),
                desc(CashSnapshot.snapshot_id),
            )
            .limit(1)
        )
        return cast(CashSnapshot | None, session.scalars(stmt).one_or_none())


def _position_source_priority():
    return case(
        (PositionSnapshot.source == OrderSource.LEDGER, 0),
        (PositionSnapshot.source == OrderSource.BROKER_RECONCILED, 1),
        else_=2,
    )


def _cash_source_priority():
    return case(
        (CashSnapshot.source == OrderSource.LEDGER, 0),
        (CashSnapshot.source == OrderSource.BROKER_RECONCILED, 1),
        else_=2,
    )
