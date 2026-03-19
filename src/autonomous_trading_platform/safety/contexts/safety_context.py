from dataclasses import dataclass

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


@dataclass
class SafetyContext:
    kill_switch_service: KillSwitchService
    live_trading_gate_service: LiveTradingGateService
    order_idempotency_service: OrderIdempotencyService
    order_throttle_service: OrderThrottleService
    pre_trade_risk_service: PreTradeRiskService
    runtime_gate_service: RuntimeGateService
    shadow_mode_service: ShadowModeService
