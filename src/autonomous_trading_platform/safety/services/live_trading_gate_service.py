from __future__ import annotations

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.safety.environment_policy import (
    EnvironmentIsolationError,
    EnvironmentSafetyPolicy,
)
from autonomous_trading_platform.safety.errors import (
    BuildGateDisabledError,
    ConfigGateDisabledError,
    LiveTradingBlockedError,
)
from autonomous_trading_platform.safety.services.kill_switch_service import (
    KillSwitchService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import (
    RuntimeGateService,
)


class LiveTradingGateService:
    """
    Coordinate all required live-trading safety gates.

    Live trading is permitted only when:
    - environment/build/config policy allows it
    - target account is allowlisted
    - runtime gate is armed
    - kill switch is not enabled
    """

    def __init__(
        self,
        environment_policy: EnvironmentSafetyPolicy,
        runtime_gate_service: RuntimeGateService,
        kill_switch_service: KillSwitchService,
    ) -> None:
        self.environment_policy = environment_policy
        self.runtime_gate_service = runtime_gate_service
        self.kill_switch_service = kill_switch_service

    def assert_live_trading_allowed(self, account_id: str) -> None:
        """
        Raise if live trading is not currently permitted for the given account.
        """
        settings = self.environment_policy.settings

        if settings.trading_environment is not TradingEnvironment.LIVE:
            raise LiveTradingBlockedError(
                "Live trading gate check called while trading environment is not LIVE."
            )

        try:
            self.environment_policy.assert_environment_allowed()
            self.environment_policy.assert_account_allowed(account_id)
        except EnvironmentIsolationError as exc:
            message = str(exc)

            if "modules are not included in this build" in message:
                raise BuildGateDisabledError(message) from exc

            if "requires ENABLE_LIVE_TRADING=true" in message:
                raise ConfigGateDisabledError(message) from exc

            raise LiveTradingBlockedError(message) from exc

        self.runtime_gate_service.assert_runtime_gate_open()
        self.kill_switch_service.assert_not_enabled()

    def can_trade_live(self, account_id: str) -> bool:
        """
        Return True if live trading is allowed, else False.
        """
        try:
            self.assert_live_trading_allowed(account_id)
        except Exception:
            return False
        return True

    def get_gate_status(self, account_id: str) -> dict[str, bool]:
        """
        Return per-gate pass/fail status for inspection/CLI use.
        """
        build_and_config_ok = True
        account_ok = True
        runtime_ok = True
        kill_switch_ok = True

        try:
            self.environment_policy.assert_environment_allowed()
        except Exception:
            build_and_config_ok = False

        try:
            self.environment_policy.assert_account_allowed(account_id)
        except Exception:
            account_ok = False

        try:
            self.runtime_gate_service.assert_runtime_gate_open()
        except Exception:
            runtime_ok = False

        try:
            self.kill_switch_service.assert_not_enabled()
        except Exception:
            kill_switch_ok = False

        return {
            "build_and_config_ok": build_and_config_ok,
            "account_ok": account_ok,
            "runtime_ok": runtime_ok,
            "kill_switch_ok": kill_switch_ok,
            "overall_ok": (build_and_config_ok and account_ok and runtime_ok and kill_switch_ok),
        }
