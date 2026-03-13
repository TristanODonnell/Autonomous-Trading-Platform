from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings

settings = Settings()

# Create engine once
engine = create_engine(settings.database_url)

# Create session factory once
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_session() -> Session:
    """
    Returns a new SQLAlchemy session.
    """
    return SessionLocal()
