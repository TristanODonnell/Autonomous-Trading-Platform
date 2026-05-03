# autonomous_trading_platform/risk/risk_context.py

from __future__ import annotations

from dataclasses import dataclass

from autonomous_trading_platform.risk.correlation_service import (
    CorrelationAwareAllocationProvider,
)
from autonomous_trading_platform.risk.portfolio_vol_targeting_service import (
    PortfolioVolTargetingService,
)
from autonomous_trading_platform.risk.rebalancing_service import RebalancingService
from autonomous_trading_platform.risk.risk_alert_service import RiskAlertService
from autonomous_trading_platform.risk.risk_engine import RiskEngine


@dataclass
class RiskContext:
    risk_engine: RiskEngine
    risk_alert_service: RiskAlertService
    portfolio_vol_targeting_service: PortfolioVolTargetingService
    rebalancing_service: RebalancingService
    correlation_aware_provider: CorrelationAwareAllocationProvider | None = None
