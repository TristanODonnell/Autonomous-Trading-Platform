from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.accounting.position_snapshot import PositionSnapshot
from autonomous_trading_platform.contracts.accounting.risk_snapshot import RiskSnapshot
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class RiskLimitConfig:
    max_gross_exposure: Decimal | None = None
    max_net_exposure: Decimal | None = None
    max_leverage: Decimal | None = None
    max_portfolio_symbol_exposure_usd: Decimal | None = None
    max_portfolio_symbol_pct: Decimal | None = None


class RiskSnapshotService:
    """Computes portfolio-level risk metrics for the current cycle."""

    def compute_snapshot(
        self,
        *,
        run_id: UUID,
        timestamp,
        position_snapshot: PositionSnapshot | None,
        cash_snapshot: CashSnapshot | None,
        limits_config: RiskLimitConfig | None = None,
        drawdown_pct: float | None = None,
    ) -> RiskSnapshot:
        limits_config = limits_config or RiskLimitConfig()

        gross_exposure = self._compute_gross_exposure(position_snapshot)
        net_exposure = self._compute_net_exposure(position_snapshot)
        equity = self._extract_equity(cash_snapshot)
        leverage = self._compute_leverage(
            gross_exposure=gross_exposure,
            equity=equity,
        )
        portfolio_reader = PortfolioRiskStateReader.from_position_snapshot(
            position_snapshot,
            total_equity=equity,
        )

        limits = self._build_limits_dict(limits_config)
        utilization = self._build_utilization_dict(
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            leverage=leverage,
            portfolio_reader=portfolio_reader,
            limits_config=limits_config,
        )

        block_reasons = self._build_block_reasons(
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            leverage=leverage,
            limits_config=limits_config,
        )

        return RiskSnapshot(
            snapshot_id=uuid4(),
            run_id=run_id,
            timestamp=timestamp,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            leverage=float(leverage),
            drawdown_pct=drawdown_pct,
            limits=limits,
            utilization=utilization,
            is_blocked=len(block_reasons) > 0,
            block_reasons=block_reasons or None,
        )

    def _to_decimal(self, value: Decimal | None) -> Decimal:
        return value if value is not None else ZERO

    def _compute_gross_exposure(
        self,
        position_snapshot: PositionSnapshot | None,
    ) -> Decimal:
        if position_snapshot is None:
            return ZERO

        total = ZERO
        for position in position_snapshot.positions:
            market_value = self._to_decimal(position.market_value)
            total += abs(market_value)
        return total

    def _compute_net_exposure(
        self,
        position_snapshot: PositionSnapshot | None,
    ) -> Decimal:
        if position_snapshot is None:
            return ZERO

        total = ZERO
        for position in position_snapshot.positions:
            market_value = self._to_decimal(position.market_value)
            total += market_value
        return total

    def _extract_equity(
        self,
        cash_snapshot: CashSnapshot | None,
    ) -> Decimal:
        if cash_snapshot is None:
            return ZERO
        return self._to_decimal(cash_snapshot.equity)

    def _compute_leverage(
        self,
        *,
        gross_exposure: Decimal,
        equity: Decimal,
    ) -> Decimal:
        if equity <= ZERO:
            return ZERO
        return gross_exposure / equity

    def _build_limits_dict(
        self,
        limits_config: RiskLimitConfig,
    ) -> dict[str, Any]:
        return {
            "max_gross_exposure": limits_config.max_gross_exposure,
            "max_net_exposure": limits_config.max_net_exposure,
            "max_leverage": limits_config.max_leverage,
            "max_portfolio_symbol_exposure_usd": (limits_config.max_portfolio_symbol_exposure_usd),
            "max_portfolio_symbol_pct": limits_config.max_portfolio_symbol_pct,
        }

    def _build_utilization_dict(
        self,
        *,
        gross_exposure: Decimal,
        net_exposure: Decimal,
        leverage: Decimal,
        portfolio_reader: PortfolioRiskStateReader,
        limits_config: RiskLimitConfig,
    ) -> dict[str, Any]:
        concentration_metrics = portfolio_reader.get_concentration_metrics(
            max_portfolio_symbol_pct=limits_config.max_portfolio_symbol_pct,
        )
        return {
            "gross_exposure_pct": self._safe_ratio(
                numerator=gross_exposure,
                denominator=limits_config.max_gross_exposure,
            ),
            "net_exposure_pct": self._safe_ratio(
                numerator=abs(net_exposure),
                denominator=limits_config.max_net_exposure,
            ),
            "leverage_pct": self._safe_ratio(
                numerator=leverage,
                denominator=limits_config.max_leverage,
            ),
            "portfolio_symbol_concentration": concentration_metrics,
        }

    def _build_block_reasons(
        self,
        *,
        gross_exposure: Decimal,
        net_exposure: Decimal,
        leverage: Decimal,
        limits_config: RiskLimitConfig,
    ) -> list[str]:
        reasons: list[str] = []

        if (
            limits_config.max_gross_exposure is not None
            and gross_exposure > limits_config.max_gross_exposure
        ):
            reasons.append("gross_exposure_limit_breached")

        if (
            limits_config.max_net_exposure is not None
            and abs(net_exposure) > limits_config.max_net_exposure
        ):
            reasons.append("net_exposure_limit_breached")

        if limits_config.max_leverage is not None and leverage > limits_config.max_leverage:
            reasons.append("leverage_limit_breached")

        return reasons

    def _safe_ratio(
        self,
        *,
        numerator: Decimal,
        denominator: Decimal | None,
    ) -> float | None:
        if denominator is None or denominator <= ZERO:
            return None
        return float(numerator / denominator)
