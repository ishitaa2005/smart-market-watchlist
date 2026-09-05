"""
Watermark service for "changes since last check".

Answers one product question -- "what meaningfully changed in my
watchlist since I last checked?" -- using two tables that already exist:

  * `significance_events` (written by EventManager -- one row per
    "episode", mutated in place while active and left as-is once closed)
  * `user_watermarks` (per-user, per-symbol "last seen event" pointer)

This module owns all the read/write logic for that pointer and for
diffing it against significance_events. It is deliberately independent
of FastAPI -- no request/response objects, no HTTP status codes -- so it
can be called from a route (app/routes/changes.py), a script, or a test
with nothing but a SQLAlchemy Session.

Deliberately excluded from this module (by design, not oversight):
  * no scoring logic (SignalEngine) and no event-threshold/hysteresis
    logic (EventManager) -- this module only *reads* significance_events,
    it never creates, closes, or reinterprets one
  * no FastAPI routes, no auth -- caller passes whatever user_id it has
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import SignificanceEvent, UserWatermark, WatchlistItem

Number = Union[int, float, Decimal, None]


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


def _build_explanation(reasons: Any) -> Optional[str]:
    """
    Best-effort human-readable explanation built purely from the reason
    messages already stored on the event -- no re-scoring, no LLM call,
    just joining data that's already there.
    """
    if not reasons:
        return None
    messages = [
        reason.get("message")
        for reason in reasons
        if isinstance(reason, dict) and reason.get("message")
    ]
    if not messages:
        return None
    return " ".join(messages)


@dataclass(frozen=True)
class SymbolChange:
    """One significance event the user hasn't seen yet, ready for API use."""

    event_id: int
    symbol: str
    score: Optional[float]
    direction: Optional[str]
    price: Optional[float]
    occurred_at: datetime
    reasons: Any
    explanation: Optional[str]
    confidence: Optional[str]
    data_status: Optional[str]
    status: Optional[str]

    @classmethod
    def from_event(cls, event: SignificanceEvent) -> "SymbolChange":
        return cls(
            event_id=event.id,
            symbol=event.symbol,
            score=_to_float(event.score),
            direction=event.direction,
            price=_to_float(event.price),
            occurred_at=event.occurred_at,
            reasons=event.reasons,
            explanation=_build_explanation(event.reasons),
            confidence=event.data_confidence,
            data_status=event.data_status,
            status=event.status,
        )


class WatermarkService:
    """
    Stateless service (all state lives in the database) for reading and
    advancing per-user, per-symbol "last seen event" watermarks.
    """

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    @staticmethod
    def get_last_seen_event_id(db: Session, user_id: str, symbol: str) -> int:
        """
        The last significance_event id `user_id` has acknowledged for
        `symbol`, or 0 if they've never acknowledged one (no watermark
        row yet at all -- treated as "seen nothing").
        """
        watermark = db.get(UserWatermark, (user_id, symbol))
        return watermark.last_seen_event_id if watermark is not None else 0

    def get_changes(self, db: Session, user_id: str) -> list[SymbolChange]:
        """
        Every significance event newer than the user's watermark, for
        every symbol on their watchlist -- newest event first.

        Both active *and* closed events are included: a closed event is
        still something that happened since the user last checked, and
        closed events are never deleted from history. Events for symbols
        not on the user's watchlist are never considered.

        Read-only: never creates, updates, or deletes a watermark row.
        """
        symbols = [
            row.symbol
            for row in db.query(WatchlistItem.symbol)
            .filter(WatchlistItem.user_id == user_id)
            .all()
        ]
        if not symbols:
            return []

        watermarks = {
            watermark.symbol: watermark.last_seen_event_id
            for watermark in db.query(UserWatermark)
            .filter(UserWatermark.user_id == user_id, UserWatermark.symbol.in_(symbols))
            .all()
        }

        changes: list[SymbolChange] = []
        for symbol in symbols:
            last_seen_event_id = watermarks.get(symbol, 0)
            events = (
                db.query(SignificanceEvent)
                .filter(
                    SignificanceEvent.symbol == symbol,
                    SignificanceEvent.id > last_seen_event_id,
                )
                .order_by(SignificanceEvent.occurred_at.desc(), SignificanceEvent.id.desc())
                .all()
            )
            changes.extend(SymbolChange.from_event(event) for event in events)

        changes.sort(key=lambda change: (change.occurred_at, change.event_id), reverse=True)
        return changes

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def acknowledge(self, db: Session, user_id: str, symbol: str) -> int:
        """
        Advance `user_id`'s watermark for `symbol` to that symbol's
        latest significance event id, and return the resulting
        last_seen_event_id.

        * If the symbol has no significance events at all, the watermark
          is safely created/left at 0 -- no event is invented.
        * Never moves the watermark backwards: only advances it, and only
          when the latest event id is greater than what's already stored.
        * Idempotent: calling this again with no new events is a no-op.

        Caller is responsible for verifying `symbol` belongs to the
        user's watchlist (this service has no opinion on watchlist
        membership) and for committing the session.
        """
        latest_event_id = (
            db.query(func.max(SignificanceEvent.id))
            .filter(SignificanceEvent.symbol == symbol)
            .scalar()
        ) or 0

        watermark = db.get(UserWatermark, (user_id, symbol))
        if watermark is None:
            watermark = UserWatermark(
                user_id=user_id, symbol=symbol, last_seen_event_id=latest_event_id
            )
            db.add(watermark)
        elif latest_event_id > watermark.last_seen_event_id:
            watermark.last_seen_event_id = latest_event_id

        db.flush()
        return watermark.last_seen_event_id


# Module-level default instance, matching the `event_manager` /
# `watermark_service` shared-instance style used elsewhere in the codebase.
watermark_service = WatermarkService()