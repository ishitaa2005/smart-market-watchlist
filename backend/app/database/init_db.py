"""
Database initialization script.

Creates any tables registered on Base.metadata that don't already exist in
the target Postgres database (DATABASE_URL from .env). This is just
`Base.metadata.create_all()` — it is idempotent and safe to run multiple
times: it never drops or recreates existing tables, it only creates the
ones that are missing.

Not a replacement for real migrations (Alembic comes later) — this is a
convenience for local/dev setup so `pytest` and `uvicorn` have somewhere
to write to.

Run from the backend/ directory:
    python -m app.database.init_db
"""

from app.database import models  # noqa: F401  (import registers tables on Base.metadata)
from app.database.session import Base, engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    table_names = sorted(Base.metadata.tables.keys())
    print(f"Database initialized. Tables ensured ({len(table_names)}):")
    for name in table_names:
        print(f"  - {name}")


if __name__ == "__main__":
    init_db()
