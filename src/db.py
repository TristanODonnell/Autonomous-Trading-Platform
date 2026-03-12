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


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_session() -> Session:
    return SessionLocal()
