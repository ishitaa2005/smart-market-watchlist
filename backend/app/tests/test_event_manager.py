"""
Integration tests for the significance event manager
(app/services/event_manager.py).

These run against the real DATABASE_URL configured for the environment
(consistent with test_watchlist.py / test_database_connection.py) --
not mocked. Each test creates the instrument row(s) it needs and cleans
up significance_events + instruments in setup/teardown, so tests are
independent of run order.

No random market data anywhere -- every SignificanceInput used here is
a fixed, hand-picked value.
"""

from app.database.models import Instrument, SignificanceEvent
from app.database.session import SessionLocal
from app.services.event_manager import (
    ACTIVE_STATUS,
    CLOSED_STATUS,
    EventManager,
    EventManagerConfig,
    SignificanceInput,
)

TEST_SYMBOL = "EVTX"
OTHER_SYMBOL = "EVTY"
ALL_TEST_SYMBOLS = [TEST_SYMBOL, OTHER_SYMBOL]


def _cleanup(symbols):
    db = SessionLocal()
    try:
        db.query(SignificanceEvent).filter(SignificanceEvent.symbol.in_(symbols)).delete(
            synchronize_session=False
        )
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


def setup_function():
    _cleanup(ALL_TEST_SYMBOLS)
    for symbol in ALL_TEST_SYMBOLS:
        _create_instrument(symbol)


def teardown_function():
    _cleanup(ALL_TEST_SYMBOLS)


def _reading(
    attention_score,
    direction="UP",
    price=105.0,
    reasons=None,
    confidence="high",
    data_status="fresh",
) -> SignificanceInput:
    return SignificanceInput(
        attention_score=attention_score,
        direction=direction,
        price=price,
        reasons=reasons if reasons is not None else [{"code": "PRICE_ANOMALY", "message": "test"}],
        confidence=confidence,
        data_status=data_status,
    )


def _all_events(symbol):
    db = SessionLocal()
    try:
        return (
            db.query(SignificanceEvent)
            .filter(SignificanceEvent.symbol == symbol)
            .order_by(SignificanceEvent.id.asc())
            .all()
        )
    finally:
        db.close()


manager = EventManager()


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
def test_score_below_threshold_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(59.9))

    assert _all_events(TEST_SYMBOL) == []


def test_score_reaching_threshold_creates_one_event():
    result = manager.process(TEST_SYMBOL, _reading(60.0, direction="UP", price=110.0))

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    assert result.id == events[0].id
    assert events[0].status == ACTIVE_STATUS
    assert events[0].direction == "UP"


def test_score_above_threshold_creates_one_event():
    manager.process(TEST_SYMBOL, _reading(85.0))

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    assert events[0].status == ACTIVE_STATUS


# --------------------------------------------------------------------------- #
# Deduplication / hysteresis
# --------------------------------------------------------------------------- #
def test_repeated_processing_above_threshold_does_not_duplicate():
    manager.process(TEST_SYMBOL, _reading(70.0))
    manager.process(TEST_SYMBOL, _reading(75.0))
    manager.process(TEST_SYMBOL, _reading(90.0))

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1


def test_active_event_keeps_same_id_while_score_remains_high():
    first = manager.process(TEST_SYMBOL, _reading(65.0, direction="UP", price=101.0))
    second = manager.process(TEST_SYMBOL, _reading(80.0, direction="UP", price=103.0))

    assert first.id == second.id
    assert second.score == 80.0
    assert float(second.price) == 103.0


def test_active_event_fields_update_on_subsequent_readings():
    manager.process(TEST_SYMBOL, _reading(65.0, direction="UP", price=101.0))
    manager.process(
        TEST_SYMBOL,
        _reading(
            72.0,
            direction="DOWN",
            price=95.0,
            reasons=[{"code": "VOLUME_ANOMALY", "message": "volume spiked"}],
            confidence="medium",
        ),
    )

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    event = events[0]
    assert event.score == 72.0
    assert event.direction == "DOWN"
    assert float(event.price) == 95.0
    assert event.data_confidence == "medium"
    assert event.reasons == [{"code": "VOLUME_ANOMALY", "message": "volume spiked"}]


def test_score_between_recovery_and_event_threshold_keeps_event_active_without_duplicating():
    manager.process(TEST_SYMBOL, _reading(65.0))
    manager.process(TEST_SYMBOL, _reading(45.0))  # between 40 and 60: stays active

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    assert events[0].status == ACTIVE_STATUS
    assert events[0].score == 45.0


# --------------------------------------------------------------------------- #
# Closing / reopening
# --------------------------------------------------------------------------- #
def test_score_below_recovery_threshold_closes_the_active_event():
    manager.process(TEST_SYMBOL, _reading(70.0))
    manager.process(TEST_SYMBOL, _reading(39.9))

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    assert events[0].status == CLOSED_STATUS


def test_score_rising_above_threshold_after_closure_creates_a_new_event():
    first = manager.process(TEST_SYMBOL, _reading(70.0))
    manager.process(TEST_SYMBOL, _reading(20.0))  # closes it

    second = manager.process(TEST_SYMBOL, _reading(90.0))  # should open a new one

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 2
    assert first.id != second.id
    assert events[0].status == CLOSED_STATUS
    assert events[1].status == ACTIVE_STATUS
    assert events[1].id == second.id


def test_score_exactly_at_recovery_threshold_does_not_close():
    manager.process(TEST_SYMBOL, _reading(70.0))
    manager.process(TEST_SYMBOL, _reading(EventManagerConfig.RECOVERY_THRESHOLD))  # == 40

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    assert events[0].status == ACTIVE_STATUS


# --------------------------------------------------------------------------- #
# Invalid / stale data
# --------------------------------------------------------------------------- #
def test_stale_data_status_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(90.0, data_status="stale"))

    assert _all_events(TEST_SYMBOL) == []


def test_unavailable_data_status_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(90.0, data_status="unavailable"))

    assert _all_events(TEST_SYMBOL) == []


def test_stale_data_does_not_update_an_active_event():
    manager.process(TEST_SYMBOL, _reading(70.0, direction="UP", price=100.0))
    manager.process(TEST_SYMBOL, _reading(95.0, direction="DOWN", price=1.0, data_status="stale"))

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    # Unchanged from the first (valid) reading -- the stale one was ignored.
    assert events[0].direction == "UP"
    assert float(events[0].price) == 100.0


def test_invalid_price_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(90.0, price=0))
    manager.process(TEST_SYMBOL, _reading(90.0, price=-5))
    manager.process(TEST_SYMBOL, _reading(90.0, price=None))

    assert _all_events(TEST_SYMBOL) == []


def test_invalid_confidence_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(90.0, confidence="unknown"))
    manager.process(TEST_SYMBOL, _reading(90.0, confidence=None))

    assert _all_events(TEST_SYMBOL) == []


def test_missing_direction_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(90.0, direction=""))
    manager.process(TEST_SYMBOL, _reading(90.0, direction=None))

    assert _all_events(TEST_SYMBOL) == []


def test_out_of_range_score_creates_no_event():
    manager.process(TEST_SYMBOL, _reading(150.0))
    manager.process(TEST_SYMBOL, _reading(-10.0))

    assert _all_events(TEST_SYMBOL) == []


# --------------------------------------------------------------------------- #
# Stored fields
# --------------------------------------------------------------------------- #
def test_stored_fields_are_correct_on_creation():
    reasons = [{"code": "PRICE_ANOMALY", "message": "z-score 4.20", "value": 4.2}]
    manager.process(
        TEST_SYMBOL,
        _reading(
            77.5,
            direction="UP",
            price=123.45,
            reasons=reasons,
            confidence="high",
            data_status="fresh",
        ),
    )

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    event = events[0]
    assert event.symbol == TEST_SYMBOL
    assert event.score == 77.5
    assert event.direction == "UP"
    assert float(event.price) == 123.45
    assert event.reasons == reasons
    assert event.data_confidence == "high"
    assert event.data_status == "fresh"
    assert event.status == ACTIVE_STATUS
    assert event.occurred_at is not None


def test_occurred_at_is_stable_across_updates():
    first = manager.process(TEST_SYMBOL, _reading(65.0))
    first_occurred_at = first.occurred_at

    second = manager.process(TEST_SYMBOL, _reading(80.0))

    assert second.occurred_at == first_occurred_at


# --------------------------------------------------------------------------- #
# Multiple symbols
# --------------------------------------------------------------------------- #
def test_multiple_symbols_maintain_independent_event_state():
    manager.process(TEST_SYMBOL, _reading(80.0, direction="UP"))
    manager.process(OTHER_SYMBOL, _reading(20.0))  # stays quiet

    test_events = _all_events(TEST_SYMBOL)
    other_events = _all_events(OTHER_SYMBOL)

    assert len(test_events) == 1
    assert test_events[0].status == ACTIVE_STATUS
    assert other_events == []

    # Closing one symbol's event must not affect the other's.
    manager.process(OTHER_SYMBOL, _reading(90.0, direction="DOWN"))
    manager.process(TEST_SYMBOL, _reading(10.0))  # closes TEST_SYMBOL only

    test_events = _all_events(TEST_SYMBOL)
    other_events = _all_events(OTHER_SYMBOL)

    assert test_events[0].status == CLOSED_STATUS
    assert len(other_events) == 1
    assert other_events[0].status == ACTIVE_STATUS
    assert other_events[0].direction == "DOWN"


# --------------------------------------------------------------------------- #
# Transaction safety / explicit session usage
# --------------------------------------------------------------------------- #
def test_process_accepts_an_externally_managed_session():
    db = SessionLocal()
    try:
        event = manager.process(TEST_SYMBOL, _reading(80.0), db=db)
        assert event is not None
        # Caller owns the transaction -- nothing committed yet from our
        # point of view until we do it ourselves.
        db.commit()
    finally:
        db.close()

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
    assert events[0].status == ACTIVE_STATUS


def test_repeated_processing_with_shared_session_does_not_duplicate():
    db = SessionLocal()
    try:
        manager.process(TEST_SYMBOL, _reading(70.0), db=db)
        manager.process(TEST_SYMBOL, _reading(75.0), db=db)
        manager.process(TEST_SYMBOL, _reading(80.0), db=db)
        db.commit()
    finally:
        db.close()

    events = _all_events(TEST_SYMBOL)
    assert len(events) == 1
