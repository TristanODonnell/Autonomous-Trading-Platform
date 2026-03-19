from autonomous_trading_platform.safety.contexts.safety_context import SafetyContext
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
from autonomous_trading_platform.safety.services.shadow_mode_service import ShadowModeService


def build_safety_context(
    *,
    settings,
    environment_policy,
    risk_state_reader,
    order_activity_reader,
):
    kill_switch_service = KillSwitchService()
    runtime_gate_service = RuntimeGateService()

    live_trading_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
    )
    order_idempotency_service = OrderIdempotencyService(
        settings=settings,
        order_activity_reader=order_activity_reader,
    )
    order_throttle_service = OrderThrottleService(
        settings=settings,
        order_activity_reader=order_activity_reader,
    )
    pre_trade_risk_service = PreTradeRiskService(
        settings=settings, risk_state_reader=risk_state_reader
    )
    shadow_mode_service = ShadowModeService(settings=settings)

    return SafetyContext(
        kill_switch_service=kill_switch_service,
        live_trading_gate_service=live_trading_gate_service,
        order_idempotency_service=order_idempotency_service,
        order_throttle_service=order_throttle_service,
        pre_trade_risk_service=pre_trade_risk_service,
        runtime_gate_service=runtime_gate_service,
        shadow_mode_service=shadow_mode_service,
    )
