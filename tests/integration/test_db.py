# tests/test_db.py

import pytest
from sqlalchemy import text

import autonomous_trading_platform.db as db_module
from autonomous_trading_platform.db import get_engine


@pytest.mark.integration
def test_database_connectivity(monkeypatch):
    # match infra/.env values
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp",
    )
    # get_engine() caches the engine for the whole process. Reset it here so this test
    # builds its own, and let monkeypatch put the previous value back afterwards: without
    # that, every later test that reaches get_session() unpatched ran against this
    # Postgres (which has no tables in CI) instead of the SQLite default.
    monkeypatch.setattr(db_module, "_engine", None)
    engine = get_engine()

    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT 1")).scalar()
    finally:
        engine.dispose()

    assert value == 1
