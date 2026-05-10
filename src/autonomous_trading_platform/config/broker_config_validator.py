# config/broker_config_validator.py

from autonomous_trading_platform.config.enums import TradingEnvironment

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_BASE_URL = "https://api.alpaca.markets"


def validate_broker_environment(
    *,
    trading_environment: TradingEnvironment,
    paper_broker_api_key: str | None,
    paper_broker_api_secret: str | None,
    live_broker_api_key: str | None,
    live_broker_api_secret: str | None,
) -> None:
    missing: list[str] = []

    if trading_environment == TradingEnvironment.PAPER:
        if not paper_broker_api_key:
            missing.append("PAPER_BROKER_API_KEY")
        if not paper_broker_api_secret:
            missing.append("PAPER_BROKER_API_SECRET")

    if trading_environment == TradingEnvironment.LIVE:
        if not live_broker_api_key:
            missing.append("LIVE_BROKER_API_KEY")
        if not live_broker_api_secret:
            missing.append("LIVE_BROKER_API_SECRET")

    if missing:
        raise RuntimeError(
            "Missing required broker environment variables for "
            f"{trading_environment.value} mode: {', '.join(missing)}"
        )


def expected_alpaca_base_url(trading_environment: TradingEnvironment) -> str:
    if trading_environment is TradingEnvironment.PAPER:
        return ALPACA_PAPER_BASE_URL
    if trading_environment is TradingEnvironment.LIVE:
        return ALPACA_LIVE_BASE_URL
    raise RuntimeError(f"Unsupported trading environment: {trading_environment!r}")


def validate_alpaca_base_url(
    *,
    trading_environment: TradingEnvironment,
    alpaca_base_url: str,
) -> None:
    expected_base_url = expected_alpaca_base_url(trading_environment)
    if alpaca_base_url != expected_base_url:
        raise RuntimeError(
            "Invalid Alpaca base URL for "
            f"{trading_environment.value} mode: expected {expected_base_url}, "
            f"got {alpaca_base_url}."
        )
