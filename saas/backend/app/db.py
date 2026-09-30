from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request. Endpoints commit explicitly; anything uncommitted is discarded."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
