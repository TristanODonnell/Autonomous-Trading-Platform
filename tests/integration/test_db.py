# tests/test_db.py

import pytest
from sqlalchemy import text

from autonomous_trading_platform.db import get_engine


@pytest.mark.integration
def test_database_connectivity(monkeypatch):
    # match infra/.env values
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp",
    )
    engine = get_engine()

    with engine.connect() as conn:
        value = conn.execute(text("SELECT 1")).scalar()

    assert value == 1
