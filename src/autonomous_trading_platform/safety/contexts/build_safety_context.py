from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.safety.contexts.safety_context import SafetyContext
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)
from autonomous_trading_platform.safety.readers.sector_exposure_reader import SectorExposureReader
from autonomous_trading_platform.safety.services.kill_switch_service import KillSwitchService
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.order_idempotency_service import (
    OrderIdempotencyService,
)
from autonomous_trading_platform.safety.services.order_throttle_service import OrderThrottleService
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService
from autonomous_trading_platform.safety.services.runtime_gate_service import RuntimeGateService
from autonomous_trading_platform.safety.services.runtime_trading_guard_service import (
    RuntimeTradingGuardService,
)
from autonomous_trading_platform.safety.services.shadow_mode_service import ShadowModeService
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


def build_safety_context(
    *,
    settings,
    environment_policy,
    risk_state_reader,
    order_activity_reader,
    audit_log_repository,
    session: Session,
    symbol_sector_map: dict[str, str] | None = None,
) -> SafetyContext:
    ks_repo = KillSwitchStateRepository(session=session)
    runtime_control_repo = RuntimeControlStateRepository(session=session)

    kill_switch_service = KillSwitchService(
        repository=ks_repo,
        runtime_control_repo=runtime_control_repo,
    )
    kill_switch_service.emit_startup_audit_event(audit_repo=audit_log_repository)

    runtime_gate_service = RuntimeGateService()

    live_trading_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
    )
    runtime_trading_guard_service = RuntimeTradingGuardService(
        settings=settings,
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        live_trading_gate_service=live_trading_gate_service,
    )
    order_idempotency_service = OrderIdempotencyService(
        settings=settings,
        order_activity_reader=order_activity_reader,
        audit_log_repository=audit_log_repository,
    )
    order_throttle_service = OrderThrottleService(
        settings=settings,
        order_activity_reader=order_activity_reader,
    )
    portfolio_risk_state_reader = PortfolioRiskStateReader.from_session(session)
    sector_exposure_reader = (
        SectorExposureReader.from_portfolio_reader(
            portfolio_risk_state_reader,
            symbol_sector_map,
        )
        if symbol_sector_map is not None
        else None
    )
    pre_trade_risk_service = PreTradeRiskService(
        settings=settings,
        risk_state_reader=risk_state_reader,
        portfolio_risk_state_reader=portfolio_risk_state_reader,
        sector_exposure_reader=sector_exposure_reader,
        max_sector_exposure_pct=getattr(settings, "max_sector_exposure_pct", None),
        default_max_sector_exposure_pct=getattr(settings, "default_max_sector_exposure_pct", None),
        unknown_sector_policy=getattr(settings, "unknown_sector_policy", "reject"),
        audit_log_repo=audit_log_repository,
    )
    shadow_mode_service = ShadowModeService(settings=settings)

    return SafetyContext(
        kill_switch_service=kill_switch_service,
        live_trading_gate_service=live_trading_gate_service,
        order_idempotency_service=order_idempotency_service,
        order_throttle_service=order_throttle_service,
        pre_trade_risk_service=pre_trade_risk_service,
        runtime_gate_service=runtime_gate_service,
        runtime_trading_guard_service=runtime_trading_guard_service,
        shadow_mode_service=shadow_mode_service,
    )
