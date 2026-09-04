"""
Tests for the Stock Details API (app/routes/stocks.py, app/services/stock_service.py).

Integration tests against the real DATABASE_URL configured for the
environment (consistent with test_watchlist.py / test_changes.py /
test_event_manager.py) -- not mocked, except where a test explicitly
needs to assert *how* SignalEngine was called. Each test creates the
exact instrument/snapshot/event/watermark rows it needs and cleans them
up in setup/teardown, so tests are independent of run order.

No random market data anywhere -- every Instrument/MarketSnapshot/
SignificanceEvent used here is inserted directly with fixed, hand-picked
values (mirroring test_changes.py / test_event_manager.py, which avoid
depending on SignalEngine/EventManager internals by not going through
them for setup).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.database.models import (
    Instrument,
    MarketSnapshot,
    SignificanceEvent,
    UserWatermark,
)
from app.database.session import SessionLocal
from app.main import app
from app.routes.watchlist import DEMO_USER_ID
from app.services.event_manager import ACTIVE_STATUS, CLOSED_STATUS
from app.services.signal_engine import SignalEngineInput, analyze_attention

client = TestClient(app)

SYMBOL_WITH_EVENT = "STKA"
SYMBOL_COMPUTED = "STKB"
SYMBOL_STALE = "STKC"
SYMBOL_UNAVAILABLE = "STKD"
UNKNOWN_SYMBOL = "STKZZZ"
ALL_TEST_SYMBOLS = [
    SYMBOL_WITH_EVENT,
    SYMBOL_COMPUTED,
    SYMBOL_STALE,
    SYMBOL_UNAVAILABLE,
    UNKNOWN_SYMBOL,
]


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
        db.query(MarketSnapshot).filter(MarketSnapshot.symbol.in_(symbols)).delete(
            synchronize_session=False
        )
        db.query(Instrument).filter(Instrument.symbol.in_(symbols)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _create_instrument(symbol: str, **overrides):
    db = SessionLocal()
    try:
        defaults = dict(symbol=symbol, name="Test Instrument")
        defaults.update(overrides)
        instrument = db.get(Instrument, symbol)
        if instrument is None:
            db.add(Instrument(**defaults))
        else:
            for key, value in overrides.items():
                setattr(instrument, key, value)
        db.commit()
    finally:
        db.close()


def _create_snapshot(symbol: str, price, volume, timestamp, data_status="fresh"):
    db = SessionLocal()
    try:
        db.add(
            MarketSnapshot(
                symbol=symbol,
                price=price,
                volume=volume,
                timestamp=timestamp,
                source="test",
                data_status=data_status,
            )
        )
        db.commit()
    finally:
        db.close()


def _create_significance_event(symbol: str, status=ACTIVE_STATUS, **overrides):
    db = SessionLocal()
    try:
        defaults = dict(
            symbol=symbol,
            status=status,
            score=75.5,
            direction="UP",
            price=4000,
            reasons=[
                {
                    "code": "PRICE_ANOMALY",
                    "message": "Price movement was 3.10x normal volatility (z-score +3.10).",
                    "value": 3.1,
                }
            ],
            data_confidence="high",
            data_status="fresh",
        )
        defaults.update(overrides)
        db.add(SignificanceEvent(**defaults))
        db.commit()
    finally:
        db.close()


def setup_function():
    _cleanup(ALL_TEST_SYMBOLS)


def teardown_function():
    _cleanup(ALL_TEST_SYMBOLS)


# --------------------------------------------------------------------------- #
# Persisted-event path
# --------------------------------------------------------------------------- #
def test_existing_symbol_with_persisted_event_returns_correct_details():
    now = datetime.now(timezone.utc)
    _create_instrument(
        SYMBOL_WITH_EVENT,
        name="Test Co A",
        last_price=4000,
        last_price_at=now,
        last_volume=1_500_000,
        week52_high=4200,
        week52_low=3300,
    )
    _create_significance_event(
        SYMBOL_WITH_EVENT,
        score=75.5,
        direction="UP",
        data_confidence="high",
    )

    response = client.get(f"/stocks/{SYMBOL_WITH_EVENT}")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == SYMBOL_WITH_EVENT
    assert body["name"] == "Test Co A"
    assert body["last_price"] == 4000.0
    assert body["last_price_at"] is not None
    assert body["last_volume"] == 1_500_000.0
    assert body["week52_high"] == 4200.0
    assert body["week52_low"] == 3300.0
    assert body["attention_score"] == 75.5
    assert body["direction"] == "UP"
    assert body["confidence"] == "high"
    assert body["data_status"] == "fresh"
    assert "Price movement was 3.10x normal volatility" in body["explanation"]
    assert body["reasons"]


def test_closed_event_is_not_treated_as_the_current_signal():
    """A CLOSED event should not be shown as if it's still the live signal --
    the service should fall back to computing a fresh (likely low/neutral)
    analysis instead of surfacing stale elevated numbers."""
    now = datetime.now(timezone.utc)
    _create_instrument(
        SYMBOL_WITH_EVENT,
        last_price=100,
        last_price_at=now,
        last_volume=1_000_000,
    )
    _create_significance_event(
        SYMBOL_WITH_EVENT,
        status=CLOSED_STATUS,
        score=90.0,
        direction="UP",
    )
    _create_snapshot(SYMBOL_WITH_EVENT, price=100, volume=1_000_000, timestamp=now)

    response = client.get(f"/stocks/{SYMBOL_WITH_EVENT}")

    assert response.status_code == 200
    body = response.json()
    # Should NOT be the stale closed event's score of 90 -- a single flat
    # snapshot with no baseline stats computes to (near) zero.
    assert body["attention_score"] != 90.0


# --------------------------------------------------------------------------- #
# Computed-analysis path (no active event persisted)
# --------------------------------------------------------------------------- #
def test_symbol_without_persisted_event_computes_via_signal_engine():
    older_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    newer_ts = datetime.now(timezone.utc) - timedelta(minutes=1)

    _create_instrument(
        SYMBOL_COMPUTED,
        name="Test Co B",
        last_price=110,
        last_price_at=newer_ts,
        last_volume=3_000_000,
        avg_return=0.0,
        return_variance=0.0001,
        avg_volume=1_000_000,
        week52_high=110,
        week52_low=90,
    )
    _create_snapshot(SYMBOL_COMPUTED, price=100, volume=1_000_000, timestamp=older_ts)
    _create_snapshot(SYMBOL_COMPUTED, price=110, volume=3_000_000, timestamp=newer_ts)

    response = client.get(f"/stocks/{SYMBOL_COMPUTED}")
    assert response.status_code == 200
    body = response.json()

    # Recompute independently with the exact same inputs the service should
    # have used, and require an exact match -- this proves the route/service
    # calls the real SignalEngine rather than reimplementing its formulas.
    expected = analyze_attention(
        SignalEngineInput(
            symbol=SYMBOL_COMPUTED,
            current_price=110,
            previous_price=100,
            current_volume=3_000_000,
            avg_return=0.0,
            return_variance=0.0001,
            avg_volume=1_000_000,
            week52_high=110,
            week52_low=90,
            history_points=2,
        )
    )

    assert body["attention_score"] == expected.attention_score
    assert body["direction"] == expected.direction
    assert body["confidence"] == expected.confidence
    assert body["explanation"] == expected.explanation
    assert body["data_status"] == "fresh"
    assert body["last_price"] == 110.0
    assert body["last_volume"] == 3_000_000.0
    assert body["week52_high"] == 110.0
    assert body["week52_low"] == 90.0


def test_signal_engine_is_called_through_the_service_not_duplicated():
    older_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    newer_ts = datetime.now(timezone.utc) - timedelta(minutes=1)

    _create_instrument(
        SYMBOL_COMPUTED,
        last_price=110,
        last_price_at=newer_ts,
        last_volume=3_000_000,
        avg_return=0.0,
        return_variance=0.0001,
        avg_volume=1_000_000,
    )
    _create_snapshot(SYMBOL_COMPUTED, price=100, volume=1_000_000, timestamp=older_ts)
    _create_snapshot(SYMBOL_COMPUTED, price=110, volume=3_000_000, timestamp=newer_ts)

    with patch(
        "app.services.stock_service.analyze_attention", wraps=analyze_attention
    ) as mocked:
        response = client.get(f"/stocks/{SYMBOL_COMPUTED}")

    assert response.status_code == 200
    mocked.assert_called_once()
    called_input = mocked.call_args.args[0]
    assert isinstance(called_input, SignalEngineInput)
    assert called_input.symbol == SYMBOL_COMPUTED
    assert called_input.current_price == 110
    assert called_input.previous_price == 100
    assert called_input.current_volume == 3_000_000


# --------------------------------------------------------------------------- #
# Unknown / missing data
# --------------------------------------------------------------------------- #
def test_unknown_symbol_returns_404():
    response = client.get(f"/stocks/{UNKNOWN_SYMBOL}")
    assert response.status_code == 404


def test_lowercase_symbol_behaves_the_same_as_uppercase():
    now = datetime.now(timezone.utc)
    _create_instrument(
        SYMBOL_WITH_EVENT,
        last_price=4000,
        last_price_at=now,
        last_volume=1_500_000,
    )
    _create_significance_event(SYMBOL_WITH_EVENT, score=80.0, direction="UP")

    upper_response = client.get(f"/stocks/{SYMBOL_WITH_EVENT}")
    lower_response = client.get(f"/stocks/{SYMBOL_WITH_EVENT.lower()}")

    assert upper_response.status_code == 200
    assert lower_response.status_code == 200
    assert lower_response.json()["symbol"] == SYMBOL_WITH_EVENT
    assert lower_response.json() == upper_response.json()


def test_symbol_with_no_market_data_reports_unavailable():
    _create_instrument(SYMBOL_UNAVAILABLE, name="No Data Yet")

    response = client.get(f"/stocks/{SYMBOL_UNAVAILABLE}")

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"] == "unavailable"
    assert body["last_price"] is None
    assert body["attention_score"] is None
    assert body["direction"] is None
    assert "No market data available" in body["explanation"]


def test_stale_data_is_represented_correctly():
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    _create_instrument(
        SYMBOL_STALE,
        last_price=250,
        last_price_at=two_days_ago,
        last_volume=500_000,
    )
    _create_snapshot(SYMBOL_STALE, price=250, volume=500_000, timestamp=two_days_ago)

    response = client.get(f"/stocks/{SYMBOL_STALE}")

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"] == "stale"
    # Data is still returned -- just clearly labeled, not hidden or faked as fresh.
    assert body["last_price"] == 250.0


# --------------------------------------------------------------------------- #
# Side-effect-free guarantees
# --------------------------------------------------------------------------- #
def test_endpoint_does_not_create_a_significance_event():
    older_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    newer_ts = datetime.now(timezone.utc) - timedelta(minutes=1)
    _create_instrument(SYMBOL_COMPUTED, last_price=110, last_price_at=newer_ts)
    _create_snapshot(SYMBOL_COMPUTED, price=100, volume=1_000_000, timestamp=older_ts)
    _create_snapshot(SYMBOL_COMPUTED, price=110, volume=3_000_000, timestamp=newer_ts)

    db = SessionLocal()
    try:
        count_before = (
            db.query(SignificanceEvent)
            .filter(SignificanceEvent.symbol == SYMBOL_COMPUTED)
            .count()
        )
    finally:
        db.close()

    response = client.get(f"/stocks/{SYMBOL_COMPUTED}")
    assert response.status_code == 200

    db = SessionLocal()
    try:
        count_after = (
            db.query(SignificanceEvent)
            .filter(SignificanceEvent.symbol == SYMBOL_COMPUTED)
            .count()
        )
    finally:
        db.close()

    assert count_before == 0
    assert count_after == count_before


def test_endpoint_does_not_modify_user_watermarks():
    now = datetime.now(timezone.utc)
    _create_instrument(SYMBOL_WITH_EVENT, last_price=4000, last_price_at=now)
    _create_significance_event(SYMBOL_WITH_EVENT, score=80.0, direction="UP")

    db = SessionLocal()
    try:
        count_before = (
            db.query(UserWatermark)
            .filter(
                UserWatermark.user_id == DEMO_USER_ID,
                UserWatermark.symbol == SYMBOL_WITH_EVENT,
            )
            .count()
        )
    finally:
        db.close()

    response = client.get(f"/stocks/{SYMBOL_WITH_EVENT}")
    assert response.status_code == 200

    db = SessionLocal()
    try:
        count_after = (
            db.query(UserWatermark)
            .filter(
                UserWatermark.user_id == DEMO_USER_ID,
                UserWatermark.symbol == SYMBOL_WITH_EVENT,
            )
            .count()
        )
    finally:
        db.close()

    assert count_before == 0
    assert count_after == 0


def test_endpoint_does_not_modify_the_watchlist():
    from app.database.models import WatchlistItem

    now = datetime.now(timezone.utc)
    _create_instrument(SYMBOL_WITH_EVENT, last_price=4000, last_price_at=now)
    _create_significance_event(SYMBOL_WITH_EVENT, score=80.0, direction="UP")

    db = SessionLocal()
    try:
        count_before = (
            db.query(WatchlistItem).filter(WatchlistItem.symbol == SYMBOL_WITH_EVENT).count()
        )
    finally:
        db.close()

    response = client.get(f"/stocks/{SYMBOL_WITH_EVENT}")
    assert response.status_code == 200

    db = SessionLocal()
    try:
        count_after = (
            db.query(WatchlistItem).filter(WatchlistItem.symbol == SYMBOL_WITH_EVENT).count()
        )
    finally:
        db.close()

    assert count_before == 0
    assert count_after == 0
