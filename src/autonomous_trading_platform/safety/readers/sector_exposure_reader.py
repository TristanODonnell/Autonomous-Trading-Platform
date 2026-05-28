from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
        PortfolioRiskStateReader,
    )

ZERO = Decimal("0")
UNKNOWN_SECTOR = "UNKNOWN"


class SectorExposureReader:
    """
    Aggregates portfolio exposure by sector for pre-trade concentration checks.

    Constructed from symbol-level exposure data and a symbol→sector map.
    Immutable once built; create a fresh instance each cycle before routing orders.
    """

    UNKNOWN_SECTOR: str = UNKNOWN_SECTOR

    def __init__(
        self,
        symbol_exposures_usd: dict[str, Decimal],
        symbol_sector_map: dict[str, str],
        total_equity: Decimal,
    ) -> None:
        self._total_equity = Decimal(total_equity)
        self._symbol_sector_map: dict[str, str] = {
            s.strip().upper(): sector.strip()
            for s, sector in symbol_sector_map.items()
            if sector and sector.strip()
        }
        self._sector_exposures: dict[str, Decimal] = {}
        self._symbol_exposures: dict[str, Decimal] = {}
        self._unmapped_symbols: set[str] = set()

        for symbol, exposure in symbol_exposures_usd.items():
            normalized = symbol.strip().upper()
            value = abs(Decimal(exposure))
            if value == ZERO:
                continue
            self._symbol_exposures[normalized] = value
            sector = self._symbol_sector_map.get(normalized)
            if sector is None:
                self._unmapped_symbols.add(normalized)
                bucket = UNKNOWN_SECTOR
            else:
                bucket = sector
            self._sector_exposures[bucket] = self._sector_exposures.get(bucket, ZERO) + value

    @classmethod
    def from_portfolio_reader(
        cls,
        portfolio_reader: PortfolioRiskStateReader,
        symbol_sector_map: dict[str, str],
    ) -> SectorExposureReader:
        return cls(
            symbol_exposures_usd=portfolio_reader.get_all_symbol_exposures(),
            symbol_sector_map=symbol_sector_map,
            total_equity=portfolio_reader.total_equity,
        )

    def get_sector_exposure_usd(self, sector: str) -> Decimal:
        return self._sector_exposures.get(sector.strip(), ZERO)

    def get_sector_exposure_pct(self, sector: str) -> Decimal:
        if self._total_equity <= ZERO:
            return ZERO
        return self.get_sector_exposure_usd(sector) / self._total_equity

    def get_symbol_sector(self, symbol: str) -> str | None:
        """Return the mapped sector for a symbol, or None if unmapped."""
        return self._symbol_sector_map.get(symbol.strip().upper())

    def get_symbol_exposure_usd(self, symbol: str) -> Decimal:
        return self._symbol_exposures.get(symbol.strip().upper(), ZERO)

    def get_all_sector_exposures(self) -> dict[str, Decimal]:
        return dict(self._sector_exposures)

    def get_unmapped_symbols(self) -> set[str]:
        return set(self._unmapped_symbols)

    def get_top_concentrated_sectors(self, n: int = 10) -> list[tuple[str, Decimal]]:
        return sorted(
            self._sector_exposures.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[:n]

    def get_sector_concentration_metrics(
        self,
        *,
        sector_limits: dict[str, float] | None = None,
        default_limit: float | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        sector_limits = sector_limits or {}
        sector_detail: dict[str, dict[str, Any]] = {}
        for sector, exposure_usd in self.get_top_concentrated_sectors(top_n):
            exposure_pct = float(self.get_sector_exposure_pct(sector))
            limit_pct = sector_limits.get(sector, default_limit)
            utilization = (
                exposure_pct / limit_pct if limit_pct is not None and limit_pct > 0 else None
            )
            sector_detail[sector] = {
                "exposure_usd": float(exposure_usd),
                "exposure_pct": exposure_pct,
                "limit_pct": limit_pct,
                "utilization_pct": utilization,
            }

        over_limit = [
            s
            for s, d in sector_detail.items()
            if d["utilization_pct"] is not None and d["utilization_pct"] > 1.0
        ]
        near_limit = [
            s
            for s, d in sector_detail.items()
            if d["utilization_pct"] is not None and 0.8 < d["utilization_pct"] <= 1.0
        ]

        return {
            "sector_exposures": sector_detail,
            "over_limit_sectors": over_limit,
            "near_limit_sectors": near_limit,
            "unmapped_symbol_count": len(self._unmapped_symbols),
            "unmapped_symbols": sorted(self._unmapped_symbols),
        }

    @property
    def total_equity(self) -> Decimal:
        return self._total_equity
