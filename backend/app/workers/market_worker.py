"""
Market monitoring worker/orchestrator for the Smart Market Watchlist.

Wires the already-implemented pieces together for one polling pass:

    MarketDataProvider -> SignalEngine -> EventManager -> PostgreSQL

This module contains **no scoring math and no event lifecycle rules** --
those live in `app.services.signal_engine` and `app.services.event_manager`
respectively, and are treated here as black boxes. All this module does is:

  1. fetch the latest tick for a symbol from a `MarketDataProvider`,
  2. read that symbol's baseline/rolling statistics from the `instruments`
     table (the "existing architecture" the significance engine relies on --
     see the comments on `Instrument` in app/database/models.py),
  3. hand both to `SignalEngine.analyze_attention()`,
  4. combine the tick + the resulting `AttentionAnalysis` into the
     `SignificanceInput` shape `EventManager.process()` expects,
  5. persist the raw tick as a `MarketSnapshot` row and advance the
     instrument's "last seen" pointer, so the *next* pass has a
     `previous_price` to compare against.

Deliberately excluded from this module (by design, not oversight):
  * no FastAPI routes
  * no scheduling / polling loop / background queue (Redis, Kafka, Celery, ...)
  * no changes to SignalEngine, EventManager, or the database models
  * no re-implementation of scoring or event-threshold logic
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import Instrument, MarketSnapshot, SignificanceEvent
from app.database.session import SessionLocal
from app.services.event_manager import EventManager
from app.services.event_manager import SignificanceInput
from app.services.event_manager import event_manager as default_event_manager
from app.services.market_data import (
    MarketDataPoint,
    MarketDataProvider,
    SimulatedMarketDataProvider,
)
from app.services.signal_engine import AttentionAnalysis, SignalEngineInput, analyze_attention

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignificanceEventSummary:
    """
    Detached-safe snapshot of a `SignificanceEvent`.

    `EventManager.process()` returns a live ORM object bound to whatever
    session processed it. When the worker owns that session (the normal
    case -- see `process_symbol()`), the session is committed and closed
    before the caller ever sees the result, which expires the ORM object's
    attributes and leaves it detached; touching them afterwards raises
    `sqlalchemy.orm.exc.DetachedInstanceError`.

    This dataclass is built *while the session is still open* (right after
    `EventManager.process()` returns, before commit/close) and copies out
    the handful of plain values a caller actually needs, so
    `SymbolProcessingResult` stays safe to read at any point after
    `process_symbol()` returns -- no live session required.
    """

    id: int
    symbol: str
    status: str
    score: Optional[float]
    direction: Optional[str]
    price: Optional[float]
    confidence: Optional[str]
    data_status: Optional[str]
    occurred_at: Optional[datetime]

    @classmethod
    def from_event(cls, event: SignificanceEvent) -> "SignificanceEventSummary":
        return cls(
            id=event.id,
            symbol=event.symbol,
            status=event.status,
            score=float(event.score) if event.score is not None else None,
            direction=event.direction,
            price=float(event.price) if event.price is not None else None,
            confidence=event.data_confidence,
            data_status=event.data_status,
            occurred_at=event.occurred_at,
        )


@dataclass
class SymbolProcessingResult:
    """
    Small, useful-for-testing/logging summary of one process_symbol() run.

    `success=False` means the symbol was *not* processed (bad/unknown
    symbol, provider failure, DB error, ...) -- `error` carries a short,
    human-readable reason. It is never raised out of `process_symbols()`,
    so one bad symbol can't take down the rest of a batch.
    """

    symbol: str
    success: bool
    market_data: Optional[MarketDataPoint] = None
    attention_analysis: Optional[AttentionAnalysis] = None
    significance_event: Optional[SignificanceEventSummary] = None
    error: Optional[str] = None


class MarketMonitoringWorker:
    """
    Coordinates MarketDataProvider -> SignalEngine -> EventManager for one
    or more symbols. Holds no market/scoring/event state itself -- all of
    that lives in the injected collaborators and the database.

    Every collaborator is injected so tests can swap in fakes:

        worker = MarketMonitoringWorker(
            market_data_provider=FakeProvider(...),
            event_manager=EventManager(session_factory=test_session_factory),
            session_factory=test_session_factory,
        )
    """

    def __init__(
        self,
        market_data_provider: Optional[MarketDataProvider] = None,
        event_manager: EventManager = default_event_manager,
        session_factory=SessionLocal,
    ):
        self._provider = market_data_provider or SimulatedMarketDataProvider()
        self._event_manager = event_manager
        self._session_factory = session_factory

    # ------------------------------------------------------------------ #
    # Single symbol
    # ------------------------------------------------------------------ #
    async def process_symbol(
        self, symbol: str, db: Optional[Session] = None
    ) -> SymbolProcessingResult:
        """
        Run one symbol through the full pipeline and persist the result.

        Never raises for anticipated failures (unknown symbol, provider
        error, DB error, ...) -- those are reported on the returned
        `SymbolProcessingResult` instead, so a caller looping over many
        symbols doesn't need its own try/except around every call.

        Accepts an optional externally-managed session (mirrors
        `EventManager.process`'s `db` parameter) for tests or callers that
        want to control the transaction themselves. Without one, this
        method opens, commits/rolls back, and closes its own session --
        which is also what gives `process_symbols()` per-symbol isolation.
        """
        symbol = symbol.strip().upper()
        owns_session = db is None
        session = db if db is not None else self._session_factory()
        try:
            result = await self._process_within_session(session, symbol)
            if owns_session:
                session.commit()
            return result
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: see docstring
            if owns_session:
                session.rollback()
            logger.warning("market_worker: failed to process %s: %s", symbol, exc)
            return SymbolProcessingResult(symbol=symbol, success=False, error=str(exc))
        finally:
            if owns_session:
                session.close()

    async def _process_within_session(
        self, session: Session, symbol: str
    ) -> SymbolProcessingResult:
        instrument = session.get(Instrument, symbol)
        if instrument is None:
            raise ValueError(f"No instrument row for symbol: {symbol}")

        # 1. Latest tick from the market data layer.
        tick = await self._provider.get_latest(symbol)

        # 2. Baseline/rolling statistics -- already computed and stored on
        # the instrument row by the existing architecture; the worker only
        # reads them, it never computes them itself.
        history_points = (
            session.query(func.count(MarketSnapshot.id))
            .filter(MarketSnapshot.symbol == symbol)
            .scalar()
        )

        signal_input = SignalEngineInput(
            symbol=symbol,
            current_price=tick.price,
            previous_price=instrument.last_price,
            current_volume=tick.volume,
            avg_return=instrument.avg_return,
            return_variance=instrument.return_variance,
            avg_volume=instrument.avg_volume,
            week52_high=instrument.week52_high,
            week52_low=instrument.week52_low,
            history_points=history_points,
        )

        # 3. Score it. Pure function, no I/O -- exactly one call, no
        # reimplementation of anything inside it.
        analysis = analyze_attention(signal_input)

        # 4. Combine the tick + analysis into what EventManager expects.
        significance_input = SignificanceInput(
            attention_score=analysis.attention_score,
            direction=analysis.direction,
            price=tick.price,
            reasons=analysis.reasons,
            confidence=analysis.confidence,
            data_status=tick.data_status,
        )
        event = self._event_manager.process(symbol, significance_input, db=session)

        # Copy the fields we need out of the ORM object *now*, while the
        # session is still open, so the result is still safe to read after
        # process_symbol() commits/closes its own session (see
        # SignificanceEventSummary's docstring).
        event_summary = SignificanceEventSummary.from_event(event) if event is not None else None

        # 5. Persist the raw tick and advance the instrument's "last seen"
        # pointer so the next pass has a previous_price to compare against.
        # Plain bookkeeping -- no scoring or event-lifecycle logic here.
        session.add(
            MarketSnapshot(
                symbol=symbol,
                price=tick.price,
                volume=tick.volume,
                timestamp=tick.timestamp,
                source=tick.source,
                data_status=tick.data_status,
            )
        )
        instrument.last_price = tick.price
        instrument.last_price_at = tick.timestamp
        instrument.last_volume = tick.volume
        session.flush()

        return SymbolProcessingResult(
            symbol=symbol,
            success=True,
            market_data=tick,
            attention_analysis=analysis,
            significance_event=event_summary,
        )

    # ------------------------------------------------------------------ #
    # Multiple symbols
    # ------------------------------------------------------------------ #
    async def process_symbols(
        self, symbols: Iterable[str]
    ) -> list[SymbolProcessingResult]:
        """
        Process each symbol independently (its own session/transaction).

        A failure on one symbol is captured in that symbol's result and
        does not stop the rest of the batch from being processed.
        """
        results: list[SymbolProcessingResult] = []
        for symbol in symbols:
            try:
                results.append(await self.process_symbol(symbol))
            except Exception as exc:  # noqa: BLE001 -- last-resort safety net;
                # process_symbol() already catches its own failures, so this
                # only guards against something unexpected escaping it.
                logger.warning("market_worker: unexpected failure on %s: %s", symbol, exc)
                results.append(
                    SymbolProcessingResult(symbol=symbol, success=False, error=str(exc))
                )
        return results