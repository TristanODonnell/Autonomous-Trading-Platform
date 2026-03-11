from sqlalchemy.orm import Session


class BaseRepository:
    """
    Base class for repository objects.

    Provides shared access to the SQLAlchemy session used for
    database operations.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
