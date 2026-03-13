from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings


def get_engine() -> Engine:
    """
    Returns the shared SQLAlchemy engine.
    """
    settings = Settings()
    return create_engine(settings.database_url)


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
