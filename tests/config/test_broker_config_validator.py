import pytest

from autonomous_trading_platform.config.broker_config_validator import (
    ALPACA_LIVE_BASE_URL,
    ALPACA_PAPER_BASE_URL,
    validate_alpaca_base_url,
)
from autonomous_trading_platform.config.enums import TradingEnvironment


def test_validates_paper_alpaca_base_url() -> None:
    validate_alpaca_base_url(
        trading_environment=TradingEnvironment.PAPER,
        alpaca_base_url=ALPACA_PAPER_BASE_URL,
    )


def test_rejects_live_alpaca_base_url_in_paper_mode() -> None:
    with pytest.raises(RuntimeError, match="Invalid Alpaca base URL"):
        validate_alpaca_base_url(
            trading_environment=TradingEnvironment.PAPER,
            alpaca_base_url=ALPACA_LIVE_BASE_URL,
        )


def test_rejects_paper_alpaca_base_url_in_live_mode() -> None:
    with pytest.raises(RuntimeError, match="Invalid Alpaca base URL"):
        validate_alpaca_base_url(
            trading_environment=TradingEnvironment.LIVE,
            alpaca_base_url=ALPACA_PAPER_BASE_URL,
        )
