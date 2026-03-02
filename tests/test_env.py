import pytest

from src.config import Settings


def test_environment_loads_required_variables(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )
    settings = Settings()

    assert settings.app_env == "local"
    assert "postgresql://" in settings.database_url


def test_missing_required_env_var_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "local")

    with pytest.raises(RuntimeError):
        Settings()
