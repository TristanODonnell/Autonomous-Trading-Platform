from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from autonomous_trading_platform.config.settings import Settings


def get_engine() -> Engine:
    settings = Settings()
    engine = create_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
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
        Base.metadata.create_all(bind=engine)
    return engine


def get_session() -> Session:
    """
    Returns a new SQLAlchemy session.
    """
    session_local = sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
    )
    return session_local()
