from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings


def get_engine() -> Engine:
    """
    Creates and returns a SQLAlchemy engine
    using the DATABASE_URL from environment.
    """
    settings = Settings()
    return create_engine(settings.database_url)


def get_session() -> Session:
    session_local = sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
    )
    return session_local()
