"""
SQLAlchemy engine, session factory, and the get_db() dependency.

This module owns the database *connection* only — no models here (see
models.py, which stays a placeholder until the next step) and no business
logic. Routes/services depend on get_db() to receive a request-scoped
Session and never talk to the engine directly.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# pool_pre_ping avoids handing out dead connections (e.g. after the DB
# restarts or an idle connection is dropped) — cheap safety net for a
# long-running dev/demo process.
engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Shared declarative base. Models (next step) will subclass this."""
    pass


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a request-scoped session and guarantees
    it's closed afterwards, even if the request raises.

    Usage in a route:
        def endpoint(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
