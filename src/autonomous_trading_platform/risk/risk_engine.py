from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyExposure:
    strategy_id: str
    gross_exposure_usd: Decimal
    net_exposure_usd: Decimal  # long - short
    long_exposure_usd: Decimal
    short_exposure_usd: Decimal
    position_count: int


@dataclass(frozen=True)
class SectorExposure:
    sector: str
    gross_exposure_usd: Decimal
    weight: Decimal  # fraction of total portfolio gross exposure


@dataclass(frozen=True)
class PortfolioExposureSnapshot:
    """
    Immutable point-in-time view of portfolio exposures.
    Produced by RiskEngine.compute_exposures() each cycle.
    """

    total_gross_exposure_usd: Decimal
    total_net_exposure_usd: Decimal
    total_long_usd: Decimal
    total_short_usd: Decimal

    # Per-strategy breakdown
    by_strategy: dict[str, StrategyExposure]

    # Per-sector breakdown (populated only when symbol_sector_map is provided)
    by_sector: dict[str, SectorExposure]

    # Concentration: largest single-strategy weight (0–1)
    max_strategy_weight: Decimal

    # Herfindahl-Hirschman Index across strategies (0 = perfectly spread, 1 = all in one)
    strategy_hhi: Decimal


class RiskEngine:
    """
    Monitors portfolio exposures across strategies, sectors and factors.

    Pure computation — no side-effects, no I/O.  Call once per cycle and
    pass the result to RiskAlertService and RebalancingService.

    Args:
        positions    — {symbol: signed_quantity}  positive=long, negative=short
        prices       — {symbol: current_price_float}
        strategy_map — {symbol: strategy_id}   optional; groups by strategy
        sector_map   — {symbol: sector_name}   optional; groups by sector
    """

    def compute_exposures(
        self,
        positions: dict[str, int],
        prices: dict[str, float],
        strategy_map: dict[str, str] | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> PortfolioExposureSnapshot:

        total_long = ZERO
        total_short = ZERO

        strategy_long: dict[str, Decimal] = {}
        strategy_short: dict[str, Decimal] = {}
        strategy_count: dict[str, int] = {}
        sector_gross: dict[str, Decimal] = {}

        for symbol, qty in positions.items():
            if qty == 0:
                continue

            price = prices.get(symbol)
            if price is None:
                logger.warning(
                    "risk_engine.missing_price",
                    extra={"symbol": symbol},
                )
                continue

            notional = Decimal(str(abs(qty))) * Decimal(str(price))

            if qty > 0:
                total_long += notional
            else:
                total_short += notional

            if strategy_map is not None:
                sid = strategy_map.get(symbol, "_unassigned")
                if qty > 0:
                    strategy_long[sid] = strategy_long.get(sid, ZERO) + notional
                else:
                    strategy_short[sid] = strategy_short.get(sid, ZERO) + notional
                strategy_count[sid] = strategy_count.get(sid, 0) + 1

            if sector_map is not None:
                sec = sector_map.get(symbol, "_unknown")
                sector_gross[sec] = sector_gross.get(sec, ZERO) + notional

        total_gross = total_long + total_short
        total_net = total_long - total_short

        # Per-strategy exposures
        all_sids = set(strategy_long) | set(strategy_short) | set(strategy_count)
        by_strategy: dict[str, StrategyExposure] = {}
        for sid in all_sids:
            lng = strategy_long.get(sid, ZERO)
            sht = strategy_short.get(sid, ZERO)
            by_strategy[sid] = StrategyExposure(
                strategy_id=sid,
                gross_exposure_usd=lng + sht,
                net_exposure_usd=lng - sht,
                long_exposure_usd=lng,
                short_exposure_usd=sht,
                position_count=strategy_count.get(sid, 0),
            )

        # Per-sector exposures
        by_sector: dict[str, SectorExposure] = {}
        for sec, gross in sector_gross.items():
            weight = (gross / total_gross) if total_gross > ZERO else ZERO
            by_sector[sec] = SectorExposure(
                sector=sec,
                gross_exposure_usd=gross,
                weight=weight,
            )

        # Concentration metrics
        max_strategy_weight: Decimal = ZERO
        hhi: Decimal = ZERO
        if total_gross > ZERO and by_strategy:
            weights = [e.gross_exposure_usd / total_gross for e in by_strategy.values()]
            max_strategy_weight = max(weights)
            hhi = sum((w * w for w in weights), ZERO)

        snapshot = PortfolioExposureSnapshot(
            total_gross_exposure_usd=total_gross,
            total_net_exposure_usd=total_net,
            total_long_usd=total_long,
            total_short_usd=total_short,
            by_strategy=by_strategy,
            by_sector=by_sector,
            max_strategy_weight=max_strategy_weight,
            strategy_hhi=hhi,
        )

        logger.debug(
            "risk_engine.exposure_snapshot",
            extra={
                "total_gross_usd": float(total_gross),
                "total_net_usd": float(total_net),
                "strategy_count": len(by_strategy),
                "sector_count": len(by_sector),
                "max_strategy_weight": float(max_strategy_weight),
                "hhi": float(hhi),
            },
        )

        return snapshot
