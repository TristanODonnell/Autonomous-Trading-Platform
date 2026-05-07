from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, String, Text, create_engine, event
from sqlalchemy.dialects import sqlite as _sqlite
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

from autonomous_trading_platform.db import get_session  # noqa: E402
from tests.utilities.corporate_action_ingestion_cycle_fixture import (  # noqa: E402
    SeededCorporateActionIngestionCycleFixture,
    seed_corporate_action_ingestion_cycle_fixture,
)
from tests.utilities.feature_pipeline_cycle_fixture import (  # noqa: E402
    SeededFeaturePipelineCycleFixture,
    seed_feature_pipeline_cycle_fixture,
)
from tests.utilities.market_backfill_cycle_fixture import (  # noqa: E402
    SeededMarketBackfillCycleFixture,
    seed_market_backfill_cycle_fixture,
)
from tests.utilities.market_ingestion_cycle_fixture import (  # noqa: E402
    SeededMarketIngestionCycleFixture,
    seed_market_ingestion_cycle_fixture,
)
from tests.utilities.paper_trading_cycle_fixture import (  # noqa: E402
    SeededPaperTradingCycleFixture,
    seed_paper_trading_cycle_fixture,
)
from tests.utilities.paper_trading_golden_path_fixture import (  # noqa: E402
    SeededPaperTradingGoldenPathFixture,
    seed_paper_trading_golden_path_fixture,
)

# ── Env vars required before any app import ──────────────────────────────────
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TRADING_ENVIRONMENT", "paper")
os.environ.setdefault("NO_LIVE_TRADING", "true")
os.environ.setdefault("SHADOW_MODE_ENABLED", "false")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-1234")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

# ---------------------------------------------------------------------------
# SQLite compatibility shims for PostgreSQL-specific column types.
# Must be patched BEFORE any app models are imported so the type compiler
# sees the overrides when create_all() runs.
# ---------------------------------------------------------------------------

# JSONB → JSON
_sqlite.base.SQLiteTypeCompiler.visit_JSONB = (  # type: ignore[attr-defined]
    lambda self, type_, **kw: self.visit_JSON(JSON(), **kw)
)

# ARRAY → TEXT (stores as JSON string; sufficient for DDL-only test purposes)
_sqlite.base.SQLiteTypeCompiler.visit_ARRAY = (  # type: ignore[attr-defined]
    lambda self, type_, **kw: self.visit_TEXT(Text(), **kw)
)

# UUID → VARCHAR(36)
_sqlite.base.SQLiteTypeCompiler.visit_UUID = (  # type: ignore[attr-defined]
    lambda self, type_, **kw: self.visit_VARCHAR(String(36), **kw)
)

from autonomous_trading_platform.interfaces.rest.app import create_app  # noqa: E402
from autonomous_trading_platform.storage.sor.models import Base  # noqa: E402
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import (  # noqa: E402,F401
    RuntimeJobRuns,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (  # noqa: E402
    StrategyGovernance,
)

# ── Engine / session factory ─────────────────────────────────────────────────


_TestingSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = os.environ["JWT_ALGORITHM"]


@pytest.fixture(scope="session", autouse=True)
def create_tables() -> None:
    """Create all ORM tables once for the test session."""
    Base.metadata.create_all(bind=_engine)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    connection = _engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        nonlocal nested
        if transaction.nested and not transaction._parent.nested:
            session.expire_all()
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    app = create_app()

    def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    return TestClient(app, raise_server_exceptions=True)


# ── JWT helpers ───────────────────────────────────────────────────────────────


def make_token(role: str = "operator", sub: str = "test-user") -> str:
    payload = {
        "sub": sub,
        "role": role,
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return str(jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM))


def auth_headers(role: str = "operator") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(role=role)}"}


# ── Seed helpers ──────────────────────────────────────────────────────────────


def seed_strategy_governance(
    session: Session,
    *,
    strategy_id: str | None = None,
    state: str = "approved_for_paper_trading",
) -> StrategyGovernance:
    row = StrategyGovernance(
        strategy_id=strategy_id or str(uuid.uuid4()),
        config_hash=str(uuid.uuid4()).replace("-", "")[:64],
        current_state=state,
        experiment_id=str(uuid.uuid4()),
        source_run_id=None,
        submitted_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        submitted_by="test",
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def seeded_paper_trading_cycle_fixture(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SeededPaperTradingCycleFixture:
    return seed_paper_trading_cycle_fixture(
        session=db_session,
        data_root=tmp_path,
        monkeypatch=monkeypatch,
    )


@pytest.fixture()
def seeded_paper_trading_golden_path_fixture(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SeededPaperTradingGoldenPathFixture:
    return seed_paper_trading_golden_path_fixture(
        session=db_session,
        data_root=tmp_path,
        monkeypatch=monkeypatch,
    )


@pytest.fixture()
def seeded_market_ingestion_cycle_fixture(
    db_session,
    tmp_path,
    monkeypatch,
) -> SeededMarketIngestionCycleFixture:
    return seed_market_ingestion_cycle_fixture(
        session=db_session,
        data_root=tmp_path,
        monkeypatch=monkeypatch,
    )


@pytest.fixture()
def seeded_feature_pipeline_cycle_fixture(
    db_session,
    tmp_path,
    monkeypatch,
) -> SeededFeaturePipelineCycleFixture:
    return seed_feature_pipeline_cycle_fixture(
        session=db_session,
        data_root=tmp_path,
        monkeypatch=monkeypatch,
    )


@pytest.fixture()
def seeded_corporate_action_ingestion_cycle_fixture(
    db_session,
    tmp_path,
    monkeypatch,
) -> SeededCorporateActionIngestionCycleFixture:
    return seed_corporate_action_ingestion_cycle_fixture(
        session=db_session,
        data_root=tmp_path,
        monkeypatch=monkeypatch,
    )


@pytest.fixture()
def seeded_market_backfill_cycle_fixture(
    db_session,
    tmp_path,
    monkeypatch,
) -> SeededMarketBackfillCycleFixture:
    return seed_market_backfill_cycle_fixture(
        session=db_session,
        data_root=tmp_path,
        monkeypatch=monkeypatch,
    )
