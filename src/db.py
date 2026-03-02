# src/db.py

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.config import Settings


def get_engine() -> Engine:
    """
    Creates and returns a SQLAlchemy engine
    using the DATABASE_URL from environment.
    """
    settings = Settings()
    return create_engine(settings.database_url)
