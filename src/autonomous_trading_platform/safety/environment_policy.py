from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings


class EnvironmentIsolationError(RuntimeError):
    pass


class EnvironmentSafetyPolicy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def assert_environment_allowed(self) -> None:
        if self.settings.trading_environment is TradingEnvironment.LIVE:
            if self.settings.no_live_trading:
                raise EnvironmentIsolationError("Live trading disabled by NO_LIVE_TRADING.")
            if not self.settings.enable_live_trading:
                raise EnvironmentIsolationError("Live trading requires ENABLE_LIVE_TRADING=true.")
            if not self.settings.include_live_modules:
                raise EnvironmentIsolationError(
                    "Live trading modules are not included in this build."
                )

    def assert_account_allowed(self, account_id: str) -> None:
        if (
            self.settings.trading_environment is TradingEnvironment.PAPER
            and self.settings.paper_allowed_account_ids
            and account_id not in self.settings.paper_allowed_account_ids
        ):
            raise EnvironmentIsolationError(f"Paper account {account_id} is not allowlisted.")

        if (
            self.settings.trading_environment is TradingEnvironment.LIVE
            and self.settings.live_allowed_account_ids
            and account_id not in self.settings.live_allowed_account_ids
        ):
            raise EnvironmentIsolationError(f"Live account {account_id} is not allowlisted.")
