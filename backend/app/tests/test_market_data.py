"""
Tests for the market data provider layer (app/services/market_data.py).

Pure in-memory tests — no database, no network. async get_latest() calls
are driven with asyncio.run() directly rather than pulling in
pytest-asyncio, to keep dependencies minimal.
"""

import asyncio
from datetime import timezone
from decimal import Decimal

from app.services.market_data import (
    SIMULATOR_SOURCE,
    SimulatedMarketDataProvider,
    get_supported_instruments,
)

EXPECTED_SYMBOLS = {"TCS", "INFY", "RELIANCE", "HDFCBANK"}


def test_all_four_supported_symbols_are_available():
    symbols = {instrument["symbol"] for instrument in get_supported_instruments()}
    assert symbols == EXPECTED_SYMBOLS


def test_supported_instruments_have_name_and_baseline_price_volume():
    for instrument in get_supported_instruments():
        assert instrument["name"]
        assert instrument["price"] > 0
        assert instrument["volume"] >= 0


def test_get_latest_returns_expected_fields():
    provider = SimulatedMarketDataProvider(seed=1)
    tick = asyncio.run(provider.get_latest("TCS"))

    assert tick.symbol == "TCS"
    assert tick.name == "Tata Consultancy Services"
    assert isinstance(tick.price, Decimal)
    assert isinstance(tick.volume, int)
    assert tick.timestamp is not None
    assert tick.source
    assert tick.data_status


def test_price_is_positive_for_all_symbols():
    provider = SimulatedMarketDataProvider(seed=2)
    for symbol in EXPECTED_SYMBOLS:
        tick = asyncio.run(provider.get_latest(symbol))
        assert tick.price > 0


def test_volume_is_non_negative_for_all_symbols():
    provider = SimulatedMarketDataProvider(seed=3)
    for symbol in EXPECTED_SYMBOLS:
        tick = asyncio.run(provider.get_latest(symbol))
        assert tick.volume >= 0


def test_timestamp_is_timezone_aware_utc():
    provider = SimulatedMarketDataProvider(seed=4)
    tick = asyncio.run(provider.get_latest("INFY"))

    assert tick.timestamp.tzinfo is not None
    assert tick.timestamp.utcoffset() == timezone.utc.utcoffset(None)


def test_source_and_data_status_are_populated():
    provider = SimulatedMarketDataProvider(seed=5)
    tick = asyncio.run(provider.get_latest("RELIANCE"))

    assert tick.source == SIMULATOR_SOURCE
    assert tick.data_status == "fresh"


def test_repeated_calls_produce_small_realistic_drift():
    """Normal (non-spike) moves should be small — a few percent at most."""
    provider = SimulatedMarketDataProvider(seed=6)
    first = asyncio.run(provider.get_latest("HDFCBANK"))
    second = asyncio.run(provider.get_latest("HDFCBANK"))

    pct_move = abs((second.price - first.price) / first.price)
    assert pct_move < Decimal("0.03")  # well under the spike range


def test_spike_mechanism_produces_a_noticeably_larger_movement():
    provider = SimulatedMarketDataProvider(seed=7)

    baseline_tick = asyncio.run(provider.get_latest("TCS"))
    normal_tick = asyncio.run(provider.get_latest("TCS"))
    normal_move = abs((normal_tick.price - baseline_tick.price) / baseline_tick.price)

    provider.trigger_spike("TCS")
    spike_tick = asyncio.run(provider.get_latest("TCS"))
    spike_move = abs((spike_tick.price - normal_tick.price) / normal_tick.price)

    assert spike_move > normal_move
    assert spike_move >= Decimal("0.03")  # spikes are 6-12% by design


def test_spike_flag_only_applies_to_the_next_call():
    provider = SimulatedMarketDataProvider(seed=8)
    provider.trigger_spike("INFY")

    asyncio.run(provider.get_latest("INFY"))  # consumes the spike
    after_spike = asyncio.run(provider.get_latest("INFY"))  # should be back to normal drift
    following = asyncio.run(provider.get_latest("INFY"))

    pct_move = abs((following.price - after_spike.price) / after_spike.price)
    assert pct_move < Decimal("0.03")


def test_unsupported_symbol_raises_key_error():
    provider = SimulatedMarketDataProvider(seed=9)
    try:
        asyncio.run(provider.get_latest("NOT-A-REAL-SYMBOL"))
        assert False, "expected KeyError for an unsupported symbol"
    except KeyError:
        pass
