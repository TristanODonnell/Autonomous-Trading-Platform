from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from autonomous_trading_platform.config.settings import Settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine

    settings = Settings()
    if settings.database_url.startswith("sqlite"):
        _engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
        )
        from sqlalchemy import JSON, String, Text  # noqa: PLC0415
        from sqlalchemy.dialects import sqlite as _sqlite  # noqa: PLC0415

        from autonomous_trading_platform.storage.sor.models import Base  # noqa: PLC0415

        _sqlite.base.SQLiteTypeCompiler.visit_JSONB = (  # type: ignore[attr-defined]
            lambda self, type_, **kw: self.visit_JSON(JSON(), **kw)
        )
        _sqlite.base.SQLiteTypeCompiler.visit_ARRAY = (  # type: ignore[attr-defined]
            lambda self, type_, **kw: self.visit_TEXT(Text(), **kw)
        )
        _sqlite.base.SQLiteTypeCompiler.visit_UUID = (  # type: ignore[attr-defined]
            lambda self, type_, **kw: self.visit_VARCHAR(String(36), **kw)
        )
        Base.metadata.create_all(bind=_engine)
    else:
        _engine = create_engine(
            settings.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engine


def get_session() -> Session:
    session_local = sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
    )
    return session_local()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
