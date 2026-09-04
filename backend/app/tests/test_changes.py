"""
Tests for the "changes since last check" API
(app/routes/changes.py, app/services/watermark.py).

Integration tests against the real DATABASE_URL configured for the
environment (consistent with test_watchlist.py / test_event_manager.py) --
not mocked. Each test creates the exact instrument/watchlist/event/
watermark rows it needs and cleans them up in setup/teardown, so tests
are independent of run order.

No random market data anywhere -- every SignificanceEvent used here is
inserted directly with fixed, hand-picked values (mirroring how
test_event_manager.py avoids depending on SignalEngine/EventManager
internals by not going through them at all).
"""

from fastapi.testclient import TestClient

from app.database.models import Instrument, SignificanceEvent, UserWatermark, WatchlistItem
from app.database.session import SessionLocal
from app.main import app
from app.routes.watchlist import DEMO_USER_ID
from app.services.event_manager import ACTIVE_STATUS, CLOSED_STATUS

client = TestClient(app)

SYMBOL_A = "CHGA"
SYMBOL_B = "CHGB"
UNWATCHED_SYMBOL = "CHGZ"  # has events, but is never added to the watchlist
ALL_TEST_SYMBOLS = [SYMBOL_A, SYMBOL_B, UNWATCHED_SYMBOL]


# --------------------------------------------------------------------------- #
# Fixtures / setup-teardown
# --------------------------------------------------------------------------- #
def _cleanup(symbols):
    db = SessionLocal()
    try:
        db.query(UserWatermark).filter(
            UserWatermark.user_id == DEMO_USER_ID, UserWatermark.symbol.in_(symbols)
        ).delete(synchronize_session=False)
        db.query(SignificanceEvent).filter(SignificanceEvent.symbol.in_(symbols)).delete(
            synchronize_session=False
        )
        db.query(WatchlistItem).filter(
            WatchlistItem.user_id == DEMO_USER_ID, WatchlistItem.symbol.in_(symbols)
        ).delete(synchronize_session=False)
        db.query(Instrument).filter(Instrument.symbol.in_(symbols)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _create_instrument(symbol: str):
    db = SessionLocal()
    try:
        if db.get(Instrument, symbol) is None:
            db.add(Instrument(symbol=symbol, name="Test Instrument", last_price=100))
            db.commit()
    finally:
        db.close()


def _add_to_watchlist(symbol: str):
    db = SessionLocal()
    try:
        if db.get(WatchlistItem, (DEMO_USER_ID, symbol)) is None:
            db.add(WatchlistItem(user_id=DEMO_USER_ID, symbol=symbol))
            db.commit()
    finally:
        db.close()


def _create_event(
    symbol: str,
    score=70.0,
    direction="UP",
    price=105.0,
    reasons=None,
    confidence="high",
    data_status="fresh",
    status=ACTIVE_STATUS,
) -> int:
    """Insert a SignificanceEvent row directly (bypassing EventManager --
    this suite tests the watermark/changes layer, not event creation) and
    return its id."""
    db = SessionLocal()
    try:
        event = SignificanceEvent(
            symbol=symbol,
            score=score,
            direction=direction,
            price=price,
            reasons=reasons
            if reasons is not None
            else [{"code": "PRICE_ANOMALY", "message": "Price moved sharply."}],
            data_confidence=confidence,
            data_status=data_status,
            status=status,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id
    finally:
        db.close()


def _get_watermark(symbol: str):
    db = SessionLocal()
    try:
        return db.get(UserWatermark, (DEMO_USER_ID, symbol))
    finally:
        db.close()


def setup_function():
    _cleanup(ALL_TEST_SYMBOLS)
    for symbol in (SYMBOL_A, SYMBOL_B, UNWATCHED_SYMBOL):
        _create_instrument(symbol)
    _add_to_watchlist(SYMBOL_A)
    _add_to_watchlist(SYMBOL_B)
    # UNWATCHED_SYMBOL deliberately never added to the watchlist.


def teardown_function():
    _cleanup(ALL_TEST_SYMBOLS)


# --------------------------------------------------------------------------- #
# GET /watchlist/changes -- no changes / empty states
# --------------------------------------------------------------------------- #
def test_no_events_returns_empty_list():
    response = client.get("/watchlist/changes")

    assert response.status_code == 200
    body = response.json()
    assert [c for c in body if c["symbol"] in (SYMBOL_A, SYMBOL_B)] == []


def test_no_watermark_yet_is_treated_as_zero():
    event_id = _create_event(SYMBOL_A, score=80.0)
    assert _get_watermark(SYMBOL_A) is None  # confirm: genuinely no row yet

    response = client.get("/watchlist/changes")

    assert response.status_code == 200
    ids = [c["event_id"] for c in response.json() if c["symbol"] == SYMBOL_A]
    assert ids == [event_id]


# --------------------------------------------------------------------------- #
# GET /watchlist/changes -- new events appear, with the fields the API promises
# --------------------------------------------------------------------------- #
def test_new_event_appears_in_changes_with_expected_fields():
    event_id = _create_event(
        SYMBOL_A,
        score=77.5,
        direction="UP",
        price=123.45,
        reasons=[{"code": "PRICE_ANOMALY", "message": "z-score 4.20"}],
        confidence="high",
        data_status="fresh",
        status=ACTIVE_STATUS,
    )

    response = client.get("/watchlist/changes")

    assert response.status_code == 200
    matches = [c for c in response.json() if c["symbol"] == SYMBOL_A]
    assert len(matches) == 1
    change = matches[0]
    assert change["event_id"] == event_id
    assert change["symbol"] == SYMBOL_A
    assert change["score"] == 77.5
    assert change["direction"] == "UP"
    assert change["price"] == 123.45
    assert change["occurred_at"] is not None
    assert change["reasons"] == [{"code": "PRICE_ANOMALY", "message": "z-score 4.20"}]
    assert change["explanation"] == "z-score 4.20"
    assert change["confidence"] == "high"
    assert change["data_status"] == "fresh"
    assert change["status"] == ACTIVE_STATUS


def test_closed_events_still_appear_in_changes():
    event_id = _create_event(SYMBOL_A, score=30.0, status=CLOSED_STATUS)

    response = client.get("/watchlist/changes")

    matches = [c for c in response.json() if c["symbol"] == SYMBOL_A]
    assert len(matches) == 1
    assert matches[0]["event_id"] == event_id
    assert matches[0]["status"] == CLOSED_STATUS


# --------------------------------------------------------------------------- #
# GET /watchlist/changes -- read-only
# --------------------------------------------------------------------------- #
def test_get_changes_does_not_modify_the_watermark():
    _create_event(SYMBOL_A, score=80.0)
    assert _get_watermark(SYMBOL_A) is None

    client.get("/watchlist/changes")
    client.get("/watchlist/changes")  # calling it twice for good measure

    assert _get_watermark(SYMBOL_A) is None  # still no watermark row at all


# --------------------------------------------------------------------------- #
# GET /watchlist/changes -- multiple events / multiple symbols
# --------------------------------------------------------------------------- #
def test_multiple_events_for_one_symbol_are_all_returned_newest_first():
    first_id = _create_event(SYMBOL_A, score=65.0, status=CLOSED_STATUS)
    second_id = _create_event(SYMBOL_A, score=90.0, status=ACTIVE_STATUS)

    response = client.get("/watchlist/changes")

    ids = [c["event_id"] for c in response.json() if c["symbol"] == SYMBOL_A]
    assert ids == [second_id, first_id]  # newest first


def test_multiple_watchlist_symbols_have_independent_watermarks():
    a_id = _create_event(SYMBOL_A, score=80.0)
    b_id = _create_event(SYMBOL_B, score=85.0)

    # Acknowledge only SYMBOL_A.
    ack_response = client.post(f"/watchlist/{SYMBOL_A}/ack")
    assert ack_response.status_code == 200
    assert ack_response.json()["last_seen_event_id"] == a_id

    response = client.get("/watchlist/changes")
    body = response.json()

    assert [c for c in body if c["symbol"] == SYMBOL_A] == []
    b_matches = [c for c in body if c["symbol"] == SYMBOL_B]
    assert len(b_matches) == 1
    assert b_matches[0]["event_id"] == b_id


def test_events_for_unwatched_symbols_never_leak_into_changes():
    _create_event(UNWATCHED_SYMBOL, score=95.0)

    response = client.get("/watchlist/changes")

    symbols_returned = {c["symbol"] for c in response.json()}
    assert UNWATCHED_SYMBOL not in symbols_returned


# --------------------------------------------------------------------------- #
# POST /watchlist/{symbol}/ack
# --------------------------------------------------------------------------- #
def test_ack_advances_watermark_and_hides_seen_events():
    event_id = _create_event(SYMBOL_A, score=80.0)

    response = client.post(f"/watchlist/{SYMBOL_A}/ack")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == SYMBOL_A
    assert body["acknowledged"] is True
    assert body["last_seen_event_id"] == event_id

    watermark = _get_watermark(SYMBOL_A)
    assert watermark is not None
    assert watermark.last_seen_event_id == event_id

    changes = client.get("/watchlist/changes").json()
    assert [c for c in changes if c["symbol"] == SYMBOL_A] == []


def test_new_event_after_ack_appears_again():
    _create_event(SYMBOL_A, score=80.0)
    client.post(f"/watchlist/{SYMBOL_A}/ack")

    second_event_id = _create_event(SYMBOL_A, score=90.0)

    changes = client.get("/watchlist/changes").json()
    matches = [c for c in changes if c["symbol"] == SYMBOL_A]
    assert len(matches) == 1
    assert matches[0]["event_id"] == second_event_id


def test_repeated_ack_is_idempotent():
    event_id = _create_event(SYMBOL_A, score=80.0)

    first = client.post(f"/watchlist/{SYMBOL_A}/ack")
    second = client.post(f"/watchlist/{SYMBOL_A}/ack")
    third = client.post(f"/watchlist/{SYMBOL_A}/ack")

    assert first.json()["last_seen_event_id"] == event_id
    assert second.json()["last_seen_event_id"] == event_id
    assert third.json()["last_seen_event_id"] == event_id

    watermark = _get_watermark(SYMBOL_A)
    assert watermark.last_seen_event_id == event_id


def test_ack_never_moves_watermark_backwards():
    _create_event(SYMBOL_A, score=70.0)
    latest_id = _create_event(SYMBOL_A, score=90.0)

    client.post(f"/watchlist/{SYMBOL_A}/ack")
    watermark_after_first_ack = _get_watermark(SYMBOL_A)
    assert watermark_after_first_ack.last_seen_event_id == latest_id

    # Ack again with no new events -- must stay exactly where it is, not
    # regress to some earlier/lower id.
    response = client.post(f"/watchlist/{SYMBOL_A}/ack")

    assert response.json()["last_seen_event_id"] == latest_id
    assert _get_watermark(SYMBOL_A).last_seen_event_id == latest_id


def test_ack_with_no_events_safely_initializes_watermark_without_inventing_an_event():
    assert _get_watermark(SYMBOL_A) is None

    response = client.post(f"/watchlist/{SYMBOL_A}/ack")

    assert response.status_code == 200
    body = response.json()
    assert body["last_seen_event_id"] == 0

    watermark = _get_watermark(SYMBOL_A)
    assert watermark is not None
    assert watermark.last_seen_event_id == 0


def test_ack_for_symbol_not_on_watchlist_returns_404():
    _create_event(UNWATCHED_SYMBOL, score=95.0)

    response = client.post(f"/watchlist/{UNWATCHED_SYMBOL}/ack")

    assert response.status_code == 404
    assert _get_watermark(UNWATCHED_SYMBOL) is None


def test_ack_is_case_insensitive_like_other_watchlist_routes():
    event_id = _create_event(SYMBOL_A, score=80.0)

    response = client.post(f"/watchlist/{SYMBOL_A.lower()}/ack")

    assert response.status_code == 200
    assert response.json()["symbol"] == SYMBOL_A
    assert response.json()["last_seen_event_id"] == event_id