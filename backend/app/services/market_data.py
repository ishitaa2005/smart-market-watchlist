"""
Market data provider abstraction + a simulated implementation.

No external APIs, no HTTP calls, no background workers — this module just
produces plausible-looking price/volume ticks in-process so the rest of
the app (watchlist display now, and a later significance engine) has
something realistic to work against without needing a real market feed.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class MarketDataPoint:
    """A single 'latest data' response for one instrument."""

    symbol: str
    name: str
    price: Decimal
    volume: int
    timestamp: datetime
    source: str
    data_status: str


class MarketDataProvider(ABC):
    """
    Abstract provider interface. Any implementation (this simulator, or a
    real API-backed one later) exposes just this one method — callers
    don't need to know which kind they have.
    """

    @abstractmethod
    async def get_latest(self, symbol: str) -> MarketDataPoint:
        ...


SIMULATOR_SOURCE = "simulated-market-data"

# Baseline seed data for the four supported instruments. Also used by
# get_supported_instruments() to seed the instruments table later.
_BASELINE_INSTRUMENTS: dict[str, dict] = {
    "TCS": {
        "name": "Tata Consultancy Services",
        "price": Decimal("3800.00"),
        "volume": 1_200_000,
    },
    "INFY": {
        "name": "Infosys",
        "price": Decimal("1550.00"),
        "volume": 2_000_000,
    },
    "RELIANCE": {
        "name": "Reliance Industries",
        "price": Decimal("2900.00"),
        "volume": 3_500_000,
    },
    "HDFCBANK": {
        "name": "HDFC Bank",
        "price": Decimal("1650.00"),
        "volume": 4_000_000,
    },
}


def get_supported_instruments() -> list[dict]:
    """
    Static seed data: symbol/name/baseline price+volume for every
    instrument the simulator knows about. Intended for seeding the
    `instruments` table — not used by get_latest() directly.
    """
    return [
        {
            "symbol": symbol,
            "name": data["name"],
            "price": data["price"],
            "volume": data["volume"],
        }
        for symbol, data in _BASELINE_INSTRUMENTS.items()
    ]


class SimulatedMarketDataProvider(MarketDataProvider):
    """
    In-memory random-walk simulator.

    Each symbol keeps its own running (price, volume) state, so repeated
    calls drift realistically instead of jumping around independently
    each time. Movements come from a per-symbol RNG seeded from the
    provider's `seed`, so a given provider instance produces a
    reproducible sequence of ticks — useful for deterministic tests —
    while different symbols still move independently of each other.
    """

    def __init__(self, seed: int = 42):
        self._state: dict[str, dict] = {
            symbol: {"price": data["price"], "volume": data["volume"]}
            for symbol, data in _BASELINE_INSTRUMENTS.items()
        }
        self._rngs: dict[str, random.Random] = {
            symbol: random.Random(f"{seed}-{symbol}") for symbol in _BASELINE_INSTRUMENTS
        }
        self._pending_spike: dict[str, bool] = {}

    def _resolve_symbol(self, symbol: str) -> str:
        symbol = symbol.strip().upper()
        if symbol not in self._state:
            raise KeyError(f"Unsupported symbol: {symbol}")
        return symbol

    def trigger_spike(self, symbol: str) -> None:
        """
        Mark `symbol` to produce an unusually large price/volume move on
        its *next* get_latest() call only (the flag is consumed once).

        This is just a bigger simulated jump — no anomaly detection or
        scoring lives here. It exists so tests/demo code can reliably
        produce an "interesting" tick to react to later.
        """
        symbol = self._resolve_symbol(symbol)
        self._pending_spike[symbol] = True

    async def get_latest(self, symbol: str) -> MarketDataPoint:
        symbol = self._resolve_symbol(symbol)
        rng = self._rngs[symbol]
        state = self._state[symbol]

        is_spike = self._pending_spike.pop(symbol, False)

        if is_spike:
            # Large, clearly-distinguishable move: ~6-12% price swing in a
            # random direction, with volume 4-8x baseline.
            pct_change = rng.uniform(0.06, 0.12) * rng.choice([-1, 1])
            volume_multiplier = rng.uniform(4.0, 8.0)
        else:
            # Small realistic drift: a fraction of a percent per tick,
            # roughly centered on no change.
            pct_change = rng.gauss(0, 0.004)
            volume_multiplier = rng.uniform(0.85, 1.15)

        new_price = state["price"] * (Decimal(1) + Decimal(str(pct_change)))
        # Keep price positive no matter how unlucky the draw — floor well above zero.
        new_price = max(new_price, state["price"] * Decimal("0.01"))
        new_price = new_price.quantize(Decimal("0.01"))

        new_volume = max(int(state["volume"] * Decimal(str(volume_multiplier))), 0)

        state["price"] = new_price
        state["volume"] = new_volume

        return MarketDataPoint(
            symbol=symbol,
            name=_BASELINE_INSTRUMENTS[symbol]["name"],
            price=new_price,
            volume=new_volume,
            timestamp=datetime.now(timezone.utc),
            source=SIMULATOR_SOURCE,
            data_status="fresh",
        )
