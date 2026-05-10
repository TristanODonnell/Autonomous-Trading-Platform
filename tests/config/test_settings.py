import pytest

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings


def test_environment_loads_required_variables(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )

    settings = Settings()

    assert settings.app_env == "local"
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_missing_required_env_var_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "local")

    with pytest.raises(RuntimeError):
        Settings()


def test_trading_environment_defaults_to_paper(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.delenv("TRADING_ENVIRONMENT", raising=False)

    settings = Settings()

    assert settings.trading_environment is TradingEnvironment.PAPER


def test_broker_credentials_use_paper_values_in_paper_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "paper-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "paper-secret")
    monkeypatch.setenv("LIVE_BROKER_API_KEY", "live-key")
    monkeypatch.setenv("LIVE_BROKER_API_SECRET", "live-secret")

    settings = Settings()

    assert settings.broker_api_key == "paper-key"
    assert settings.broker_api_secret == "paper-secret"
    assert settings.alpaca_base_url == "https://paper-api.alpaca.markets"


def test_broker_credentials_use_live_values_in_live_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "paper-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "paper-secret")
    monkeypatch.setenv("LIVE_BROKER_API_KEY", "live-key")
    monkeypatch.setenv("LIVE_BROKER_API_SECRET", "live-secret")

    settings = Settings()

    assert settings.broker_api_key == "live-key"
    assert settings.broker_api_secret == "live-secret"
    assert settings.alpaca_base_url == "https://api.alpaca.markets"


def test_missing_paper_broker_credentials_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.delenv("PAPER_BROKER_API_KEY", raising=False)
    monkeypatch.delenv("PAPER_BROKER_API_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="PAPER_BROKER_API_KEY"):
        Settings()


def test_missing_live_broker_credentials_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.delenv("LIVE_BROKER_API_KEY", raising=False)
    monkeypatch.delenv("LIVE_BROKER_API_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="LIVE_BROKER_API_KEY"):
        Settings()


def test_allowed_account_ids_parse_as_list(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.setenv("PAPER_ALLOWED_ACCOUNT_IDS", "acct-1, acct-2")

    settings = Settings()

    assert settings.paper_allowed_account_ids == ["acct-1", "acct-2"]


def test_missing_allowed_account_ids_defaults_to_empty_list(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    monkeypatch.delenv("PAPER_ALLOWED_ACCOUNT_IDS", raising=False)

    settings = Settings()

    assert settings.paper_allowed_account_ids == []
