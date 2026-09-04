"""
API tests for the Watchlist CRUD endpoints.

These are integration tests against the real DATABASE_URL configured for
the environment (consistent with the rest of this suite, e.g.
test_database_connection.py) — not mocked. Each test creates the exact
instrument/watchlist rows it needs and cleans them up in setup/teardown,
so tests are independent of run order and don't leak state into each
other or into a real demo-user watchlist.
"""

from fastapi.testclient import TestClient

from app.database.models import Instrument, WatchlistItem
from app.database.session import SessionLocal
from app.main import app
from app.routes.watchlist import DEMO_USER_ID

client = TestClient(app)

TEST_SYMBOL = "TESTX"
OTHER_SYMBOL = "TESTY"
MISSING_SYMBOL = "NOSUCHSYM"
ALL_TEST_SYMBOLS = [TEST_SYMBOL, OTHER_SYMBOL, MISSING_SYMBOL]


def _cleanup(symbols):
    """Delete any watchlist items / instruments this test suite created."""
    db = SessionLocal()
    try:
        db.query(WatchlistItem).filter(
            WatchlistItem.user_id == DEMO_USER_ID,
            WatchlistItem.symbol.in_(symbols),
        ).delete(synchronize_session=False)
        db.query(Instrument).filter(Instrument.symbol.in_(symbols)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _create_instrument(symbol: str, name: str = "Test Instrument"):
    db = SessionLocal()
    try:
        if db.get(Instrument, symbol) is None:
            db.add(
                Instrument(symbol=symbol, name=name, last_price=100, last_volume=1000)
            )
            db.commit()
    finally:
        db.close()


def setup_function():
    _cleanup(ALL_TEST_SYMBOLS)


def teardown_function():
    _cleanup(ALL_TEST_SYMBOLS)


def test_add_existing_instrument_succeeds():
    _create_instrument(TEST_SYMBOL)

    response = client.post(f"/watchlist/{TEST_SYMBOL}")

    assert response.status_code == 201
    body = response.json()
    assert body["symbol"] == TEST_SYMBOL
    assert body["added"] is True


def test_adding_same_instrument_twice_returns_409():
    _create_instrument(TEST_SYMBOL)

    first = client.post(f"/watchlist/{TEST_SYMBOL}")
    assert first.status_code == 201

    second = client.post(f"/watchlist/{TEST_SYMBOL}")
    assert second.status_code == 409


def test_adding_nonexistent_instrument_returns_404():
    response = client.post(f"/watchlist/{MISSING_SYMBOL}")
    assert response.status_code == 404


def test_get_watchlist_returns_added_items():
    _create_instrument(TEST_SYMBOL, name="Test Co")
    add_response = client.post(f"/watchlist/{TEST_SYMBOL}")
    assert add_response.status_code == 201

    response = client.get("/watchlist")

    assert response.status_code == 200
    items = response.json()
    matching = [item for item in items if item["symbol"] == TEST_SYMBOL]
    assert len(matching) == 1
    assert matching[0]["name"] == "Test Co"
    assert matching[0]["last_price"] == "100"


def test_get_watchlist_returns_empty_list_when_no_items():
    db = SessionLocal()
    try:
        db.query(WatchlistItem).filter(WatchlistItem.user_id == DEMO_USER_ID).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/watchlist")

    assert response.status_code == 200
    assert response.json() == []


def test_delete_existing_item_succeeds():
    _create_instrument(TEST_SYMBOL)
    add_response = client.post(f"/watchlist/{TEST_SYMBOL}")
    assert add_response.status_code == 201

    response = client.delete(f"/watchlist/{TEST_SYMBOL}")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == TEST_SYMBOL
    assert body["removed"] is True

    get_response = client.get("/watchlist")
    symbols = [item["symbol"] for item in get_response.json()]
    assert TEST_SYMBOL not in symbols


def test_delete_nonexistent_item_returns_404():
    response = client.delete(f"/watchlist/{TEST_SYMBOL}")
    assert response.status_code == 404
