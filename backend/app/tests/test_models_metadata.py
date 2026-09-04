"""
Verifies the SQLAlchemy model metadata matches the agreed schema:
all 6 tables exist, primary keys (including composite ones) are correct,
and the significance_events(symbol, occurred_at) index is present.

This is a metadata-level check (no live DB needed) — it fails fast if a
model is renamed, a PK is dropped, or the index is missing, without
requiring Postgres to be running.
"""

from app.database.session import Base

# Importing models registers them on Base.metadata as a side effect.
from app.database import models  # noqa: F401

EXPECTED_TABLES = {
    "users",
    "instruments",
    "watchlist_items",
    "market_snapshots",
    "significance_events",
    "user_watermarks",
}


def test_all_expected_tables_are_registered():
    assert EXPECTED_TABLES.issubset(set(Base.metadata.tables.keys()))


def test_users_primary_key():
    table = Base.metadata.tables["users"]
    assert {c.name for c in table.primary_key.columns} == {"id"}


def test_instruments_primary_key():
    table = Base.metadata.tables["instruments"]
    assert {c.name for c in table.primary_key.columns} == {"symbol"}


def test_watchlist_items_composite_primary_key():
    table = Base.metadata.tables["watchlist_items"]
    assert {c.name for c in table.primary_key.columns} == {"user_id", "symbol"}


def test_market_snapshots_primary_key():
    table = Base.metadata.tables["market_snapshots"]
    assert {c.name for c in table.primary_key.columns} == {"id"}


def test_significance_events_primary_key():
    table = Base.metadata.tables["significance_events"]
    assert {c.name for c in table.primary_key.columns} == {"id"}


def test_user_watermarks_composite_primary_key():
    table = Base.metadata.tables["user_watermarks"]
    assert {c.name for c in table.primary_key.columns} == {"user_id", "symbol"}


def test_significance_events_symbol_occurred_at_index_exists():
    table = Base.metadata.tables["significance_events"]
    index_column_sets = {
        tuple(col.name for col in index.columns) for index in table.indexes
    }
    assert ("symbol", "occurred_at") in index_column_sets


def test_foreign_keys_point_to_expected_tables():
    watchlist_items = Base.metadata.tables["watchlist_items"]
    fk_targets = {fk.target_fullname for fk in watchlist_items.foreign_keys}
    assert fk_targets == {"users.id", "instruments.symbol"}

    market_snapshots = Base.metadata.tables["market_snapshots"]
    assert {fk.target_fullname for fk in market_snapshots.foreign_keys} == {
        "instruments.symbol"
    }

    significance_events = Base.metadata.tables["significance_events"]
    assert {fk.target_fullname for fk in significance_events.foreign_keys} == {
        "instruments.symbol"
    }
