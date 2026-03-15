import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.safety.environment_policy import (
    EnvironmentIsolationError,
    EnvironmentSafetyPolicy,
)


def _base_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )


def test_paper_environment_is_allowed_by_default(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")

    settings = Settings()
    policy = EnvironmentSafetyPolicy(settings)

    policy.assert_environment_allowed()


def test_live_environment_blocked_when_no_live_trading_enabled(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "true")

    settings = Settings()
    policy = EnvironmentSafetyPolicy(settings)

    with pytest.raises(EnvironmentIsolationError):
        policy.assert_environment_allowed()


def test_live_environment_blocked_when_live_not_explicitly_enabled(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "true")

    settings = Settings()
    policy = EnvironmentSafetyPolicy(settings)

    with pytest.raises(EnvironmentIsolationError):
        policy.assert_environment_allowed()


def test_live_environment_blocked_when_live_modules_not_included(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "false")

    settings = Settings()
    policy = EnvironmentSafetyPolicy(settings)

    with pytest.raises(EnvironmentIsolationError):
        policy.assert_environment_allowed()


def test_paper_account_not_in_allowlist_raises(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("PAPER_ALLOWED_ACCOUNT_IDS", "paper-1")

    settings = Settings()
    policy = EnvironmentSafetyPolicy(settings)

    with pytest.raises(EnvironmentIsolationError):
        policy.assert_account_allowed("paper-2")


def test_empty_allowlist_allows_account(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.delenv("PAPER_ALLOWED_ACCOUNT_IDS", raising=False)

    settings = Settings()
    policy = EnvironmentSafetyPolicy(settings)

    policy.assert_account_allowed("any-paper-account")
