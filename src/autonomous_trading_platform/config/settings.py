import os

from dotenv import load_dotenv

from autonomous_trading_platform.config.enums import TradingEnvironment

load_dotenv()


class Settings:
    """
    Environment configuration loader with explicit trading-environment safety flags.
    """

    def __init__(self) -> None:
        self.app_env = self._get_required("APP_ENV")
        self.database_url = self._get_required("DATABASE_URL")

        self.trading_environment = TradingEnvironment(
            os.getenv("TRADING_ENVIRONMENT", TradingEnvironment.PAPER.value)
        )
        self.no_live_trading = self._get_bool("NO_LIVE_TRADING", default=True)
        self.enable_live_trading = self._get_bool("ENABLE_LIVE_TRADING", default=False)
        self.include_live_modules = self._get_bool("INCLUDE_LIVE_MODULES", default=False)

        self.paper_allowed_account_ids = self._get_list("PAPER_ALLOWED_ACCOUNT_IDS")
        self.live_allowed_account_ids = self._get_list("LIVE_ALLOWED_ACCOUNT_IDS")

        self.paper_broker_api_key = os.getenv("PAPER_BROKER_API_KEY")
        self.paper_broker_api_secret = os.getenv("PAPER_BROKER_API_SECRET")

        self.live_broker_api_key = os.getenv("LIVE_BROKER_API_KEY")
        self.live_broker_api_secret = os.getenv("LIVE_BROKER_API_SECRET")

    def _get_required(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise RuntimeError(f"Missing required environment variable: {key}")
        return value

    def _get_bool(self, key: str, default: bool) -> bool:
        value = os.getenv(key)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _get_list(self, key: str) -> list[str]:
        value = os.getenv(key)

        if not value:
            return []

        return [item.strip() for item in value.split(",") if item.strip()]
