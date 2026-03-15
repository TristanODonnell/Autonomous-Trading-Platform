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
