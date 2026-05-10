from __future__ import annotations

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.safety.environment_policy import (
    EnvironmentIsolationError,
    EnvironmentSafetyPolicy,
)
from autonomous_trading_platform.safety.errors import (
    ConfigGateDisabledError,
    LiveTradingBlockedError,
)
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import RuntimeGateService


class RuntimeTradingGuardService:
    """
    Fail-closed guard for broker-backed trading mode execution.

    Paper trading is allowed without runtime arming. Live trading requires the
    configured live environment, live credentials, config enablement, and an
    armed runtime gate before execution-sensitive work may proceed.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        environment_policy: EnvironmentSafetyPolicy,
        runtime_gate_service: RuntimeGateService,
        live_trading_gate_service: LiveTradingGateService,
    ) -> None:
        self.settings = settings
        self.environment_policy = environment_policy
        self.runtime_gate_service = runtime_gate_service
        self.live_trading_gate_service = live_trading_gate_service

    def assert_trading_mode_allowed(
        self,
        *,
        account_id: str | None = None,
        run_type: RunType | str | None = None,
    ) -> None:
        trading_environment = self.settings.trading_environment

        if trading_environment not in {
            TradingEnvironment.PAPER,
            TradingEnvironment.LIVE,
        }:
            raise LiveTradingBlockedError(
                f"Unsupported trading environment: {trading_environment!r}."
            )

        self._assert_run_type_matches_environment(run_type)
        self._assert_credentials_present()

        if trading_environment is TradingEnvironment.PAPER:
            self._assert_account_allowed(account_id)
            return

        self._assert_live_config_enabled()

        if account_id is not None:
            self.live_trading_gate_service.assert_live_trading_allowed(account_id)
            return

        try:
            self.environment_policy.assert_environment_allowed()
        except EnvironmentIsolationError as exc:
            raise LiveTradingBlockedError(str(exc)) from exc

        self.runtime_gate_service.assert_runtime_gate_open()

    def _assert_run_type_matches_environment(self, run_type: RunType | str | None) -> None:
        if run_type is None:
            return

        try:
            normalized_run_type = RunType(run_type)
        except ValueError as exc:
            raise LiveTradingBlockedError(f"Unsupported trading run type: {run_type!r}.") from exc
        expected_run_type = (
            RunType.PAPER
            if self.settings.trading_environment is TradingEnvironment.PAPER
            else RunType.LIVE
        )

        if normalized_run_type is not expected_run_type:
            raise LiveTradingBlockedError(
                "Trading run type does not match configured environment: "
                f"run_type={normalized_run_type.value}, "
                f"trading_environment={self.settings.trading_environment.value}."
            )

    def _assert_credentials_present(self) -> None:
        if not self.settings.broker_api_key or not self.settings.broker_api_secret:
            raise ConfigGateDisabledError(
                "Missing broker credentials for the configured trading environment."
            )

    def _assert_account_allowed(self, account_id: str | None) -> None:
        if account_id is None:
            return

        try:
            self.environment_policy.assert_account_allowed(account_id)
        except EnvironmentIsolationError as exc:
            raise LiveTradingBlockedError(str(exc)) from exc

    def _assert_live_config_enabled(self) -> None:
        if self.settings.no_live_trading:
            raise ConfigGateDisabledError("Live trading disabled by NO_LIVE_TRADING.")

        if not self.settings.enable_live_trading:
            raise ConfigGateDisabledError("Live trading requires ENABLE_LIVE_TRADING=true.")
