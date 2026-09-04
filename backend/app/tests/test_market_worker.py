"""
Tests for the market monitoring worker/orchestrator
(app/workers/market_worker.py).

Integration tests against the real DATABASE_URL configured for the
environment (consistent with test_event_manager.py / test_watchlist.py) --
not mocked at the DB layer. The market-data *provider* is faked with fixed,
hand-picked ticks so nothing here depends on random market behavior; every
score below is worked out by hand from SignalEngineConfig's constants.

Each test creates the instrument rows it needs and cleans up
significance_events / market_snapshots / instruments in setup/teardown, so
tests are independent of run order.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.database.models import Instrument, MarketSnapshot, SignificanceEvent
from app.database.session import SessionLocal
from app.services.event_manager import ACTIVE_STATUS
from app.services.market_data import MarketDataPoint, MarketDataProvider
from app.services.signal_engine import analyze_attention as real_analyze_attention
from app.workers.market_worker import MarketMonitoringWorker

SYMBOL_A = "WRKA"  # big move -> expected to cross the event threshold
SYMBOL_B = "WRKB"  # small move -> expected to stay quiet
SYMBOL_MISSING = "WRKX"  # deliberately never given an Instrument row
ALL_TEST_SYMBOLS = [SYMBOL_A, SYMBOL_B, SYMBOL_MISSING]


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeMarketDataProvider(MarketDataProvider):
    """
    Deterministic stand-in for SimulatedMarketDataProvider.

    Returns whichever fixed MarketDataPoint the test configured for a
    symbol, or raises whichever exception was configured -- no randomness,
    so tests never depend on random market behavior.
    """

    def __init__(self, ticks: dict | None = None):
        self._ticks = dict(ticks or {})

    async def get_latest(self, symbol: str) -> MarketDataPoint:
        value = self._ticks.get(symbol)
        if value is None:
            raise KeyError(f"No fake tick configured for {symbol}")
        if isinstance(value, Exception):
            raise value
        return value


class RecordingEventManager:
    """
    Records every call it receives instead of touching the database, so
    tests can assert on exactly what the worker handed to EventManager
    without depending on EventManager's own (separately-tested) logic.
    """

    def __init__(self):
        self.calls = []

    def process(self, symbol, signal_result, db=None):
        self.calls.append((symbol, signal_result, db))
        return None


def _tick(symbol: str, price, volume: int, data_status: str = "fresh") -> MarketDataPoint:
    return MarketDataPoint(
        symbol=symbol,
        name="Test Instrument",
        price=Decimal(str(price)),
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        source="fake-provider",
        data_status=data_status,
    )


# --------------------------------------------------------------------------- #
# Fixtures / setup-teardown
# --------------------------------------------------------------------------- #
def _cleanup(symbols):
    db = SessionLocal()
    try:
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
        if db.get(Instrument, symbol) is None:
            defaults = dict(
                symbol=symbol,
                name="Test Instrument",
                last_price=Decimal("100.00"),
                last_volume=Decimal("1000000"),
                avg_return=Decimal("0.0"),
                return_variance=Decimal("0.0001"),  # 1% baseline daily std dev
                avg_volume=Decimal("1000000"),
                week52_high=Decimal("110.00"),
                week52_low=Decimal("90.00"),
            )
            defaults.update(overrides)
            db.add(Instrument(**defaults))
            db.commit()
    finally:
        db.close()


def setup_function():
    _cleanup(ALL_TEST_SYMBOLS)
    _create_instrument(SYMBOL_A)
    _create_instrument(SYMBOL_B)
    # SYMBOL_MISSING intentionally gets no Instrument row -- used to
    # exercise the "unknown symbol" failure path.


def teardown_function():
    _cleanup(ALL_TEST_SYMBOLS)


# --------------------------------------------------------------------------- #
# Single symbol: full pipeline
# --------------------------------------------------------------------------- #
def test_process_symbol_runs_full_pipeline_and_creates_event():
    """
    previous_price=100, avg_return=0, std_dev=0.01, tick price=115 (+15%):
        z = (0.15 - 0) / 0.01 = 15  -> price_score saturates at 100
    tick volume=5,000,000 vs avg_volume=1,000,000:
        ratio = 5.0 -> volume_score saturates at 100
    attention_score = 0.40*100 + 0.25*100 + 0.20*0 + 0.15*0 = 65.0
    65.0 >= EVENT_THRESHOLD (60.0) -> a new active event is opened.
    """
    provider = FakeMarketDataProvider({SYMBOL_A: _tick(SYMBOL_A, "115.00", 5_000_000)})
    worker = MarketMonitoringWorker(market_data_provider=provider)

    result = asyncio.run(worker.process_symbol(SYMBOL_A))

    assert result.success is True
    assert result.symbol == SYMBOL_A
    assert result.error is None

    # Market data flowed through.
    assert result.market_data.price == Decimal("115.00")

    # SignalEngine ran (real, untouched implementation) and produced the
    # expected score.
    assert result.attention_analysis.attention_score == 65.0
    assert result.attention_analysis.direction == "UP"

    # EventManager (real, untouched implementation) opened an event from it.
    assert result.significance_event is not None
    assert result.significance_event.status == ACTIVE_STATUS
    assert float(result.significance_event.score) == 65.0
    assert result.significance_event.direction == "UP"

    # And everything landed in PostgreSQL.
    db = SessionLocal()
    try:
        events = db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL_A).all()
        assert len(events) == 1
        assert events[0].status == ACTIVE_STATUS

        snapshots = (
            db.query(MarketSnapshot).filter(MarketSnapshot.symbol == SYMBOL_A).all()
        )
        assert len(snapshots) == 1
        assert float(snapshots[0].price) == 115.0
        assert snapshots[0].data_status == "fresh"

        instrument = db.get(Instrument, SYMBOL_A)
        assert float(instrument.last_price) == 115.0
        assert instrument.last_volume == 5_000_000
    finally:
        db.close()


def test_process_symbol_below_threshold_produces_no_event():
    """
    previous_price=100, tick price=101 (+1%): z = (0.01-0)/0.01 = 1.0 ->
    price_score = (1/3)*100 ~= 33.33; volume ratio = 1.0 -> volume_score=0.
    attention_score ~= 13.33, well under the 60.0 event threshold.
    """
    provider = FakeMarketDataProvider({SYMBOL_B: _tick(SYMBOL_B, "101.00", 1_000_000)})
    worker = MarketMonitoringWorker(market_data_provider=provider)

    result = asyncio.run(worker.process_symbol(SYMBOL_B))

    assert result.success is True
    assert result.significance_event is None

    db = SessionLocal()
    try:
        assert (
            db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL_B).count()
            == 0
        )
        # The tick is still recorded even when no event fires.
        assert (
            db.query(MarketSnapshot).filter(MarketSnapshot.symbol == SYMBOL_B).count() == 1
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# EventManager receives the expected combined input
# --------------------------------------------------------------------------- #
def test_event_manager_receives_expected_combined_input():
    tick = _tick(SYMBOL_A, "115.00", 5_000_000)
    provider = FakeMarketDataProvider({SYMBOL_A: tick})
    spy = RecordingEventManager()
    worker = MarketMonitoringWorker(market_data_provider=provider, event_manager=spy)

    result = asyncio.run(worker.process_symbol(SYMBOL_A))

    assert result.success is True
    assert len(spy.calls) == 1
    symbol, significance_input, db = spy.calls[0]

    assert symbol == SYMBOL_A
    assert db is not None  # the worker's own session was passed through
    assert significance_input.attention_score == result.attention_analysis.attention_score
    assert significance_input.direction == result.attention_analysis.direction
    assert significance_input.confidence == result.attention_analysis.confidence
    assert significance_input.reasons == result.attention_analysis.reasons
    assert significance_input.price == tick.price
    assert significance_input.data_status == tick.data_status


# --------------------------------------------------------------------------- #
# No duplicated business logic: SignalEngine is called, not reimplemented
# --------------------------------------------------------------------------- #
def test_worker_delegates_to_signal_engine_exactly_once():
    provider = FakeMarketDataProvider({SYMBOL_A: _tick(SYMBOL_A, "115.00", 5_000_000)})
    worker = MarketMonitoringWorker(market_data_provider=provider)

    with patch(
        "app.workers.market_worker.analyze_attention", wraps=real_analyze_attention
    ) as mock_analyze:
        result = asyncio.run(worker.process_symbol(SYMBOL_A))

    assert mock_analyze.call_count == 1
    assert result.success is True
    assert result.attention_analysis.attention_score == 65.0


# --------------------------------------------------------------------------- #
# Multiple symbols processed independently
# --------------------------------------------------------------------------- #
def test_process_symbols_processes_each_symbol_independently():
    provider = FakeMarketDataProvider(
        {
            SYMBOL_A: _tick(SYMBOL_A, "115.00", 5_000_000),  # big move -> event
            SYMBOL_B: _tick(SYMBOL_B, "101.00", 1_000_000),  # small move -> no event
        }
    )
    worker = MarketMonitoringWorker(market_data_provider=provider)

    results = asyncio.run(worker.process_symbols([SYMBOL_A, SYMBOL_B]))

    assert [r.symbol for r in results] == [SYMBOL_A, SYMBOL_B]
    assert all(r.success for r in results)
    assert results[0].significance_event is not None
    assert results[0].significance_event.status == ACTIVE_STATUS
    assert results[1].significance_event is None

    db = SessionLocal()
    try:
        assert (
            db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL_A).count()
            == 1
        )
        assert (
            db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL_B).count()
            == 0
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Error resilience
# --------------------------------------------------------------------------- #
def test_provider_failure_is_captured_as_a_failed_result_not_raised():
    provider = FakeMarketDataProvider({SYMBOL_A: RuntimeError("feed unavailable")})
    worker = MarketMonitoringWorker(market_data_provider=provider)

    result = asyncio.run(worker.process_symbol(SYMBOL_A))

    assert result.success is False
    assert "feed unavailable" in result.error
    assert result.market_data is None
    assert result.significance_event is None

    # Nothing should have been persisted for the failed symbol.
    db = SessionLocal()
    try:
        assert (
            db.query(SignificanceEvent).filter(SignificanceEvent.symbol == SYMBOL_A).count()
            == 0
        )
        assert (
            db.query(MarketSnapshot).filter(MarketSnapshot.symbol == SYMBOL_A).count() == 0
        )
    finally:
        db.close()


def test_unknown_symbol_fails_without_touching_the_provider():
    provider = FakeMarketDataProvider({})  # no tick configured for anything
    worker = MarketMonitoringWorker(market_data_provider=provider)

    result = asyncio.run(worker.process_symbol(SYMBOL_MISSING))

    assert result.success is False
    # The instrument lookup happens before the provider is ever called, so
    # the error is the specific "no instrument row" message, not whatever
    # the (empty) fake provider would have raised for an unconfigured symbol.
    assert result.error == f"No instrument row for symbol: {SYMBOL_MISSING}"


def test_one_symbol_failure_does_not_stop_the_rest_of_the_batch():
    provider = FakeMarketDataProvider(
        {
            SYMBOL_A: _tick(SYMBOL_A, "115.00", 5_000_000),
            SYMBOL_B: _tick(SYMBOL_B, "101.00", 1_000_000),
            # SYMBOL_MISSING has no Instrument row -> will fail.
        }
    )
    worker = MarketMonitoringWorker(market_data_provider=provider)

    results = asyncio.run(worker.process_symbols([SYMBOL_MISSING, SYMBOL_A, SYMBOL_B]))

    assert [r.symbol for r in results] == [SYMBOL_MISSING, SYMBOL_A, SYMBOL_B]

    missing_result, a_result, b_result = results
    assert missing_result.success is False
    assert missing_result.error is not None

    # The failure above must not have prevented the other two from running.
    assert a_result.success is True
    assert a_result.significance_event is not None
    assert b_result.success is True


# --------------------------------------------------------------------------- #
# Dependency injection: externally-managed session
# --------------------------------------------------------------------------- #
def test_process_symbol_accepts_an_externally_managed_session():
    provider = FakeMarketDataProvider({SYMBOL_A: _tick(SYMBOL_A, "115.00", 5_000_000)})
    worker = MarketMonitoringWorker(market_data_provider=provider)

    db = SessionLocal()
    try:
        result = asyncio.run(worker.process_symbol(SYMBOL_A, db=db))
        assert result.success is True
        # Caller owns the transaction -- nothing committed until we do it.
        db.commit()
    finally:
        db.close()

    verify_db = SessionLocal()
    try:
        assert (
            verify_db.query(SignificanceEvent)
            .filter(SignificanceEvent.symbol == SYMBOL_A)
            .count()
            == 1
        )
    finally:
        verify_db.close()