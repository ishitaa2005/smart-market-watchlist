"""Integration tests for development-only deterministic demo scenarios."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.database.models import Instrument, MarketSnapshot, SignificanceEvent, UserWatermark
from app.database.session import SessionLocal
from app.main import app

client = TestClient(app)
SYMBOL = "TCS"


def _reset_symbol() -> None:
    db = SessionLocal()
    try:
        db.query(UserWatermark).filter(UserWatermark.symbol == SYMBOL).delete()
        db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL).delete()
        db.query(MarketSnapshot).filter(MarketSnapshot.symbol == SYMBOL).delete()
        instrument = db.get(Instrument, SYMBOL)
        instrument.last_price = Decimal("3800.00")
        instrument.last_price_at = datetime.now(timezone.utc)
        instrument.last_volume = Decimal("1200000")
        instrument.avg_return = Decimal("0")
        instrument.return_variance = Decimal("0.000016")
        instrument.avg_volume = Decimal("1200000")
        instrument.week52_high = Decimal("4560.00")
        instrument.week52_low = Decimal("3040.00")
        db.commit()
    finally:
        db.close()


def setup_function():
    _reset_symbol()


def teardown_function():
    _reset_symbol()


def test_normal_scenario_stays_quiet():
    response = client.post(f"/demo/scenarios/normal/{SYMBOL}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["attention_score"] < 60
    assert body["event_created"] is False


def test_price_shock_creates_event_through_worker_pipeline():
    response = client.post(f"/demo/scenarios/price_shock/{SYMBOL}")

    assert response.status_code == 200
    body = response.json()
    assert body["attention_score"] >= 80
    assert body["direction"] == "UP"
    assert body["event_created"] is True
    assert "price" in body["explanation"].lower()


def test_volume_and_relative_scenarios_expose_their_real_signal_reasons():
    volume = client.post(f"/demo/scenarios/volume_anomaly/{SYMBOL}").json()
    _reset_symbol()
    relative = client.post(f"/demo/scenarios/relative_performance/{SYMBOL}").json()

    assert "volume" in volume["explanation"].lower()
    assert "benchmark" in relative["explanation"].lower()


def test_stale_high_score_tick_cannot_create_event():
    response = client.post(f"/demo/scenarios/stale_data/{SYMBOL}")

    assert response.status_code == 200
    body = response.json()
    assert body["attention_score"] >= 60
    assert body["data_status"] == "stale"
    assert body["event_created"] is False

    db = SessionLocal()
    try:
        assert db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL).count() == 0
        snapshot = db.query(MarketSnapshot).filter(MarketSnapshot.symbol == SYMBOL).one()
        assert snapshot.data_status == "stale"
    finally:
        db.close()


def test_unknown_scenario_and_unwatched_symbol_are_rejected():
    assert client.post(f"/demo/scenarios/not_real/{SYMBOL}").status_code == 422
    assert client.post("/demo/scenarios/normal/UNKNOWN").status_code == 404


def test_demo_endpoint_is_hidden_in_production():
    with patch(
        "app.routes.demo.get_settings",
        return_value=SimpleNamespace(environment="production"),
    ):
        response = client.post(f"/demo/scenarios/price_shock/{SYMBOL}")

    assert response.status_code == 404