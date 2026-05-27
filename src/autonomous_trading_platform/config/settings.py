import os
from decimal import Decimal

from dotenv import load_dotenv

from autonomous_trading_platform.config.broker_config_validator import (
    expected_alpaca_base_url,
    validate_alpaca_base_url,
    validate_broker_environment,
)
from autonomous_trading_platform.config.enums import TradingEnvironment

load_dotenv()


class Settings:
    """
    Environment configuration loader with explicit trading-environment safety flags.
    """

    def __init__(self) -> None:
        self.app_env = self._get_required("APP_ENV")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.database_url = self._get_required("DATABASE_URL")
        self.trading_environment = TradingEnvironment(
            os.getenv("TRADING_ENVIRONMENT", TradingEnvironment.PAPER.value)
        )
        self.no_live_trading = self._get_bool("NO_LIVE_TRADING", default=True)
        self.enable_live_trading = self._get_bool("ENABLE_LIVE_TRADING", default=False)
        self.include_live_modules = self._get_bool("INCLUDE_LIVE_MODULES", default=False)
        self.shadow_mode_enabled = self._get_bool("SHADOW_MODE_ENABLED", default=True)
        self.paper_allowed_account_ids = self._get_list("PAPER_ALLOWED_ACCOUNT_IDS")
        self.live_allowed_account_ids = self._get_list("LIVE_ALLOWED_ACCOUNT_IDS")

        self.paper_broker_api_key = os.getenv("PAPER_BROKER_API_KEY")
        self.paper_broker_api_secret = os.getenv("PAPER_BROKER_API_SECRET")

        self.live_broker_api_key = os.getenv("LIVE_BROKER_API_KEY")
        self.live_broker_api_secret = os.getenv("LIVE_BROKER_API_SECRET")

        self.universe_rebalance_cadence = os.getenv(
            "UNIVERSE_REBALANCE_CADENCE",
            "daily",
        )
        self.initial_capital = self._get_float("INITIAL_CAPITAL", 10000.0)

        self.max_gross_exposure = self._get_float("MAX_GROSS_EXPOSURE", 100000.0)
        self.max_symbol_exposure = self._get_float("MAX_SYMBOL_EXPOSURE", 10000.0)
        self.max_portfolio_symbol_exposure_usd = self._get_optional_float(
            "MAX_PORTFOLIO_SYMBOL_EXPOSURE_USD",
        )
        self.max_portfolio_symbol_pct = self._get_optional_float("MAX_PORTFOLIO_SYMBOL_PCT")
        self.max_daily_notional_traded = self._get_float("MAX_DAILY_NOTIONAL_TRADED", 25000.0)
        self.max_reserved_cash = self._get_float("MAX_RESERVED_CASH", 25000.0)

        self.max_orders_per_hour = self._get_int("MAX_ORDERS_PER_HOUR", 20)
        self.max_orders_per_bar = self._get_int("MAX_ORDERS_PER_BAR", 1)

        self.block_repeat_orders_same_bar = self._get_bool(
            "BLOCK_REPEAT_ORDERS_SAME_BAR", default=True
        )
        self.idempotency_deduplication_window_minutes = self._get_int(
            "IDEMPOTENCY_DEDUPLICATION_WINDOW_MINUTES",
            15,
        )

        self.enable_cash_snapshot_freshness_check = self._get_bool(
            "ENABLE_CASH_SNAPSHOT_FRESHNESS_CHECK", default=True
        )
        self.max_cash_snapshot_age_seconds = self._get_int(
            "MAX_CASH_SNAPSHOT_AGE_SECONDS",
            600 if self.trading_environment == TradingEnvironment.LIVE else 900,
        )

        self.market_bar_freshness_sla_seconds = self._get_int(
            "MARKET_BAR_FRESHNESS_SLA_SECONDS",
            30,
        )
        self.trading_evaluation_timeout_seconds = self._get_int(
            "TRADING_EVALUATION_TIMEOUT_SECONDS",
            60,
        )
        self.order_submission_timeout_seconds = self._get_int(
            "ORDER_SUBMISSION_TIMEOUT_SECONDS",
            60,
        )
        self.reconciliation_timeout_seconds = self._get_int(
            "RECONCILIATION_TIMEOUT_SECONDS",
            120,
        )

        self.skip_evaluation_on_ingestion_failure = self._get_bool(
            "SKIP_EVALUATION_ON_INGESTION_FAILURE",
            default=True,
        )
        self.hold_positions_on_evaluation_failure = self._get_bool(
            "HOLD_POSITIONS_ON_EVALUATION_FAILURE",
            default=True,
        )
        self.freeze_trading_on_reconciliation_failure = self._get_bool(
            "FREEZE_TRADING_ON_RECONCILIATION_FAILURE",
            default=True,
        )
        self.paper_runtime_order_safeguards_enabled = self._get_bool(
            "PAPER_RUNTIME_ORDER_SAFEGUARDS_ENABLED",
            default=False,
        )
        self.paper_runtime_max_order_qty = self._get_decimal(
            "PAPER_RUNTIME_MAX_ORDER_QTY",
            Decimal("1"),
        )
        self.paper_runtime_max_order_notional = self._get_decimal(
            "PAPER_RUNTIME_MAX_ORDER_NOTIONAL",
            Decimal("1.00"),
        )
        self.paper_runtime_max_limit_price = self._get_decimal(
            "PAPER_RUNTIME_MAX_LIMIT_PRICE",
            Decimal("1.00"),
        )
        self.paper_runtime_allowed_symbols = self._get_list(
            "PAPER_RUNTIME_ALLOWED_SYMBOLS",
        )
        self.paper_runtime_require_limit_orders = self._get_bool(
            "PAPER_RUNTIME_REQUIRE_LIMIT_ORDERS",
            default=True,
        )
        self.trading_cycle_timeout_seconds = self._get_int(
            "TRADING_CYCLE_TIMEOUT_SECONDS",
            240,
        )
        self.trading_cycle_sla_seconds = self._get_int(
            "TRADING_CYCLE_SLA_SECONDS",
            240,
        )
        self.trading_cycle_timeout_seconds = self._get_int(
            "TRADING_CYCLE_TIMEOUT_SECONDS",
            300,
        )
        self.trading_cycle_retry_attempts = self._get_int(
            "TRADING_CYCLE_RETRY_ATTEMPTS",
            3,
        )
        self.trading_cycle_retry_delay_seconds = self._get_int(
            "TRADING_CYCLE_RETRY_DELAY_SECONDS",
            30,
        )
        self.max_net_exposure = self._get_float("MAX_NET_EXPOSURE", 50000.0)
        self.max_leverage = self._get_float("MAX_LEVERAGE", 2.0)

        self.validate_broker_config = self._get_bool(
            "VALIDATE_BROKER_CONFIG",
            default=True,
        )

        # ----------------------------------------------------
        # VALIDATE AFTER LOAD
        # ----------------------------------------------------
        if self.validate_broker_config:
            validate_broker_environment(
                trading_environment=self.trading_environment,
                paper_broker_api_key=self.paper_broker_api_key,
                paper_broker_api_secret=self.paper_broker_api_secret,
                live_broker_api_key=self.live_broker_api_key,
                live_broker_api_secret=self.live_broker_api_secret,
            )
            validate_alpaca_base_url(
                trading_environment=self.trading_environment,
                alpaca_base_url=self.alpaca_base_url,
            )

    @property
    def broker_api_key(self) -> str | None:
        if self.trading_environment == TradingEnvironment.PAPER:
            return self.paper_broker_api_key
        return self.live_broker_api_key

    @property
    def broker_api_secret(self) -> str | None:
        if self.trading_environment == TradingEnvironment.PAPER:
            return self.paper_broker_api_secret
        return self.live_broker_api_secret

    @property
    def alpaca_base_url(self) -> str:
        return expected_alpaca_base_url(self.trading_environment)

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

    def _get_int(self, key: str, default: int) -> int:
        value = os.getenv(key)

        if value is None:
            return default
        return int(value.strip())

    def _get_float(self, key: str, default: float) -> float:
        value = os.getenv(key)
        if value is None:
            return default
        return float(value.strip())

    def _get_optional_float(self, key: str) -> float | None:
        value = os.getenv(key)
        if value is None or value.strip() == "":
            return None
        return float(value.strip())

    def _get_decimal(self, key: str, default: Decimal) -> Decimal:
        value = os.getenv(key)

        if value is None:
            return default
        return Decimal(value.strip())
