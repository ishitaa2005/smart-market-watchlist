"""
Verifies the SQLAlchemy engine can establish a real connection to Postgres
using DATABASE_URL. This requires a running Postgres instance reachable at
the configured URL — it is a connectivity check, not a unit test with mocks,
by design (a green result here means "the DB layer is actually wired up
correctly", which is the whole point of this step).
"""

from sqlalchemy import text

from app.database.session import engine


def test_database_connection_can_be_established():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        assert result.scalar() == 1
