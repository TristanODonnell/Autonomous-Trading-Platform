from __future__ import annotations

import os
from collections.abc import Generator

import pytest

from autonomous_trading_platform.config.broker_config_validator import ALPACA_PAPER_BASE_URL
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient

REQUIRED_EXTERNAL_ENV_VARS = (
    "ALPACA_EXTERNAL_SMOKE_ENABLED",
    "PAPER_BROKER_API_KEY",
    "PAPER_BROKER_API_SECRET",
)


def _missing_external_env_vars() -> list[str]:
    return [name for name in REQUIRED_EXTERNAL_ENV_VARS if not os.getenv(name)]


@pytest.fixture(scope="session")
def alpaca_paper_settings() -> Settings:
    missing = _missing_external_env_vars()
    if missing:
        pytest.skip("Skipping Alpaca external smoke tests; missing env vars: " + ", ".join(missing))

    if os.getenv("ALPACA_EXTERNAL_SMOKE_ENABLED", "").strip().lower() != "true":
        pytest.skip(
            "Skipping Alpaca external smoke tests; set "
            "ALPACA_EXTERNAL_SMOKE_ENABLED=true to run real Alpaca requests."
        )

    settings = Settings()

    if settings.trading_environment is not TradingEnvironment.PAPER:
        pytest.fail("Alpaca external smoke tests must run with TRADING_ENVIRONMENT=paper.")

    if settings.alpaca_base_url != ALPACA_PAPER_BASE_URL:
        pytest.fail("Alpaca external smoke tests must use the Alpaca paper trading base URL.")

    if settings.broker_api_key != settings.paper_broker_api_key:
        pytest.fail("Alpaca external smoke tests must use paper broker credentials.")

    if settings.broker_api_secret != settings.paper_broker_api_secret:
        pytest.fail("Alpaca external smoke tests must use paper broker credentials.")

    return settings


@pytest.fixture()
def alpaca_paper_client(
    alpaca_paper_settings: Settings,
) -> Generator[AlpacaBrokerClient, None, None]:
    client = AlpacaBrokerClient(alpaca_paper_settings)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def alpaca_smoke_symbol() -> str:
    return os.getenv("ALPACA_SMOKE_SYMBOL", "AAPL").strip().upper()
