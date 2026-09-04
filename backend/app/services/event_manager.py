"""
Significance event manager for the Smart Market Watchlist.

Converts signal-engine attention results into persistent
`significance_events` rows, with hysteresis so a symbol hovering around
the threshold doesn't spam duplicate events.

Deliberately excluded from this module (by design, not oversight):
  * no FastAPI routes
  * no background worker / scheduling
  * no changes to the SignalEngine or the database models
  * no market-data fetching -- this module only *reacts* to a result
    that was already computed upstream (SignalEngine + market data tier)

This module is meant to be called from future market-monitoring code
roughly like:

    from app.services.event_manager import event_manager, SignificanceInput

    event_manager.process(
        symbol,
        SignificanceInput(
            attention_score=analysis.attention_score,
            direction=analysis.direction,
            price=tick.price,
            reasons=analysis.reasons,
            confidence=analysis.confidence,
            data_status=tick.data_status,
        ),
    )

`SignificanceInput` intentionally is a small, independent struct rather
than reusing `AttentionAnalysis` directly: the event manager needs the
current *price* and the market data layer's *data_status*, neither of
which the (deliberately market-data-agnostic) SignalEngine produces.
Future orchestration code is expected to combine a `MarketDataPoint`
and an `AttentionAnalysis` into one of these.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from app.database.models import SignificanceEvent
from app.database.session import SessionLocal

Number = Union[int, float, Decimal, None]


# --------------------------------------------------------------------------- #
# Configuration -- every tunable constant lives here so thresholds can be
# recalibrated later without touching the lifecycle logic below.
# --------------------------------------------------------------------------- #
class EventManagerConfig:
    # Score at/above which a *new* event is opened for a symbol with no
    # currently-active event.
    EVENT_THRESHOLD = 60.0

    # Score at/below which an active event is closed. Deliberately lower
    # than EVENT_THRESHOLD (hysteresis): a score sitting between the two
    # keeps an already-open event alive without opening new ones, which
    # is what stops a score oscillating around a single cutoff from
    # generating a new event on every tick.
    RECOVERY_THRESHOLD = 40.0

    # data_status values (from the market-data layer) that are trusted
    # enough to create/update an event from. Anything else (stale,
    # unavailable, error, missing, ...) is treated as not-actionable.
    VALID_DATA_STATUSES = frozenset({"fresh"})

    # data_confidence values (from the SignalEngine) that are considered
    # meaningful at all. This is intentionally permissive -- "low"
    # confidence still reflects a real (if less certain) computation and
    # is allowed to open/update events; only missing/garbage confidence
    # values are rejected.
    VALID_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


ACTIVE_STATUS = "active"
CLOSED_STATUS = "closed"


@dataclass(frozen=True)
class SignificanceInput:
    """
    Everything the event manager needs for one processing pass, sourced
    from the SignalEngine's `AttentionAnalysis` plus the market-data
    tier's latest tick for the same symbol.
    """

    attention_score: Number
    direction: str
    price: Number
    reasons: Any  # JSON-serializable structure, passed straight to storage
    confidence: str
    data_status: str = "fresh"


def _to_float(value: Number) -> Optional[float]:
    """Best-effort conversion to float. None / garbage -> None. Never raises."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _is_valid_input(signal_result: SignificanceInput, cfg: type[EventManagerConfig]) -> bool:
    """
    Guards against stale/unavailable/invalid data. Never invents a
    value -- if anything required is missing or out of range, the
    caller's data is simply not actionable this tick.
    """
    if signal_result.data_status not in cfg.VALID_DATA_STATUSES:
        return False
    if signal_result.confidence not in cfg.VALID_CONFIDENCE_LEVELS:
        return False

    score = _to_float(signal_result.attention_score)
    if score is None or not (0.0 <= score <= 100.0):
        return False

    price = _to_float(signal_result.price)
    if price is None or price <= 0:
        return False

    if not signal_result.direction:
        return False

    return True


class EventManager:
    """
    Stateless service (all state lives in the database) that applies
    threshold + hysteresis rules to turn attention scores into
    `significance_events` rows.

    Independent of FastAPI: `process()` can be called with an
    externally-managed `Session` (e.g. from a request or a test), or
    with none at all, in which case it opens, commits/rolls back, and
    closes its own session -- suitable for being called directly from a
    future polling worker that isn't inside a request lifecycle.
    """

    def __init__(
        self,
        config: type[EventManagerConfig] = EventManagerConfig,
        session_factory=SessionLocal,
    ):
        self.config = config
        self._session_factory = session_factory

    def process(
        self,
        symbol: str,
        signal_result: SignificanceInput,
        db: Optional[Session] = None,
    ) -> Optional[SignificanceEvent]:
        """
        Apply one attention-score reading for `symbol`.

        Returns the `SignificanceEvent` row that was created, updated,
        or closed as a result of this call, or None if nothing changed
        (score too low with no active event, or the data wasn't
        trustworthy enough to act on).
        """
        symbol = symbol.strip().upper()
        owns_session = db is None
        session = db if db is not None else self._session_factory()
        try:
            event = self._process_within_session(session, symbol, signal_result)
            if owns_session:
                session.commit()
                if event is not None:
                    session.refresh(event)
            return event
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _process_within_session(
        self, session: Session, symbol: str, signal_result: SignificanceInput
    ) -> Optional[SignificanceEvent]:
        if not _is_valid_input(signal_result, self.config):
            return None

        score = _to_float(signal_result.attention_score)
        active_event = self._get_active_event(session, symbol)

        if active_event is not None:
            if score < self.config.RECOVERY_THRESHOLD:
                self._apply_latest_reading(active_event, signal_result)
                active_event.status = CLOSED_STATUS
                session.flush()
                return active_event

            # Still elevated (whether above EVENT_THRESHOLD again or
            # simply not yet recovered) -- keep the same event alive and
            # refresh it with the latest reading. No new row, same ID.
            self._apply_latest_reading(active_event, signal_result)
            session.flush()
            return active_event

        if score >= self.config.EVENT_THRESHOLD:
            new_event = SignificanceEvent(
                symbol=symbol,
                status=ACTIVE_STATUS,
            )
            self._apply_latest_reading(new_event, signal_result)
            session.add(new_event)
            session.flush()
            return new_event

        # Below the threshold to open a new event, and none is active.
        return None

    @staticmethod
    def _get_active_event(session: Session, symbol: str) -> Optional[SignificanceEvent]:
        return (
            session.query(SignificanceEvent)
            .filter(
                SignificanceEvent.symbol == symbol,
                SignificanceEvent.status == ACTIVE_STATUS,
            )
            .order_by(SignificanceEvent.occurred_at.desc(), SignificanceEvent.id.desc())
            .first()
        )

    @staticmethod
    def _apply_latest_reading(
        event: SignificanceEvent, signal_result: SignificanceInput
    ) -> None:
        """Write the latest reading's mutable fields onto `event` in place."""
        event.score = _to_float(signal_result.attention_score)
        event.direction = signal_result.direction
        event.price = _to_float(signal_result.price)
        event.reasons = signal_result.reasons
        event.data_confidence = signal_result.confidence
        event.data_status = signal_result.data_status


# Module-level default instance, for the common case of a single shared
# manager talking to the app's normal database -- matches the
# `event_manager.process(symbol, signal_result)` call style.
event_manager = EventManager()
