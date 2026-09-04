"""
Stock Details service for the Smart Market Watchlist.

Answers one product question -- "what is the current state of this
stock, right now?" -- for a single instrument, using data that already
exists in Postgres:

  * the `instruments` row (latest known price/volume/52-week range and
    the rolling baseline statistics the SignalEngine consumes)
  * the most recent *active* `significance_events` row for the symbol,
    if the worker pipeline has already persisted one
  * the two most recent `market_snapshots` rows, used to reconstruct a
    read-only attention analysis via the existing SignalEngine when no
    active significance event is already persisted

Deliberately excluded from this module (by design, not oversight):
  * no live MarketDataProvider calls -- this endpoint only reads what's
    already persisted, so GET /stocks/{symbol} is a pure read with no
    side effects and no dependency on in-memory simulator state (a fresh
    SimulatedMarketDataProvider instance would restart from its seed
    baseline, disconnected from the instrument's real evolved price --
    calling it here would risk inventing a misleading value)
  * no EventManager calls -- never creates, updates, or closes a
    significance_events row
  * no WatermarkService calls -- never reads or writes user_watermarks
  * no re-implementation of SignalEngine's scoring formulas -- the one
    and only call to `analyze_attention()` lives here, and this module
    never duplicates its math
  * no calls to the market monitoring worker
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from app.database.models import Instrument, MarketSnapshot, SignificanceEvent
from app.services.event_manager import ACTIVE_STATUS
from app.services.signal_engine import SignalEngineInput, analyze_attention
from app.services.watermark import _build_explanation as _explanation_from_reasons

Number = Union[int, float, Decimal, None]


# --------------------------------------------------------------------------- #
# Configuration -- how old the latest known price is allowed to be before
# it's no longer considered "fresh". Kept here, not in SignalEngine or
# EventManager, since this is purely a display/freshness concern for this
# endpoint, not a scoring or event-lifecycle rule.
# --------------------------------------------------------------------------- #
class StockServiceConfig:
    FRESH_MAX_AGE_SECONDS = 5 * 60  # under 5 minutes old -> "fresh"
    DELAYED_MAX_AGE_SECONDS = 30 * 60  # under 30 minutes old -> "delayed", else "stale"


FRESH = "fresh"
DELAYED = "delayed"
STALE = "stale"
UNAVAILABLE = "unavailable"  # no known price/timestamp at all


def _to_float(value: Number) -> Optional[float]:
    """Best-effort conversion to float. None / garbage -> None. Never raises."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class StockDetails:
    """The full response shape for GET /stocks/{symbol}."""

    symbol: str
    name: Optional[str]
    last_price: Optional[float]
    last_price_at: Optional[datetime]
    last_volume: Optional[float]
    week52_high: Optional[float]
    week52_low: Optional[float]
    data_status: str  # fresh | delayed | stale | unavailable
    attention_score: Optional[float]
    direction: Optional[str]
    confidence: Optional[str]
    explanation: Optional[str]
    reasons: Any


class StockService:
    """Read-only. Never writes to the database, never mutates its inputs."""

    def get_stock_details(self, db: Session, symbol: str) -> Optional[StockDetails]:
        """
        Returns the current known state for `symbol`, or None if it isn't
        a recognized instrument (caller is expected to turn that into a
        404 -- this service has no opinion on HTTP).
        """
        symbol = symbol.strip().upper()
        instrument = db.get(Instrument, symbol)
        if instrument is None:
            return None

        data_status = self._resolve_data_status(instrument.last_price_at)

        active_event = self._get_active_event(db, symbol)
        if active_event is not None:
            return self._from_persisted_event(instrument, active_event, data_status)

        return self._from_computed_analysis(db, instrument, data_status)

    # ------------------------------------------------------------------ #
    # Data freshness
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_data_status(last_price_at: Optional[datetime]) -> str:
        if last_price_at is None:
            return UNAVAILABLE

        reference = last_price_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        age_seconds = (datetime.now(timezone.utc) - reference).total_seconds()
        age_seconds = max(age_seconds, 0.0)

        if age_seconds <= StockServiceConfig.FRESH_MAX_AGE_SECONDS:
            return FRESH
        if age_seconds <= StockServiceConfig.DELAYED_MAX_AGE_SECONDS:
            return DELAYED
        return STALE

    # ------------------------------------------------------------------ #
    # Path 1: an active significance event is already persisted -- use it
    # as-is, no recomputation.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _get_active_event(db: Session, symbol: str) -> Optional[SignificanceEvent]:
        return (
            db.query(SignificanceEvent)
            .filter(
                SignificanceEvent.symbol == symbol,
                SignificanceEvent.status == ACTIVE_STATUS,
            )
            .order_by(SignificanceEvent.occurred_at.desc(), SignificanceEvent.id.desc())
            .first()
        )

    @staticmethod
    def _from_persisted_event(
        instrument: Instrument, event: SignificanceEvent, data_status: str
    ) -> StockDetails:
        return StockDetails(
            symbol=instrument.symbol,
            name=instrument.name,
            last_price=_to_float(instrument.last_price),
            last_price_at=instrument.last_price_at,
            last_volume=_to_float(instrument.last_volume),
            week52_high=_to_float(instrument.week52_high),
            week52_low=_to_float(instrument.week52_low),
            data_status=data_status,
            attention_score=_to_float(event.score),
            direction=event.direction,
            confidence=event.data_confidence,
            explanation=_explanation_from_reasons(event.reasons),
            reasons=event.reasons,
        )

    # ------------------------------------------------------------------ #
    # Path 2: no active event persisted -- reconstruct a read-only
    # attention analysis from already-persisted history via the existing
    # SignalEngine (same inputs the worker itself would use), without
    # writing anything back.
    # ------------------------------------------------------------------ #
    def _from_computed_analysis(
        self, db: Session, instrument: Instrument, data_status: str
    ) -> StockDetails:
        recent_snapshots = (
            db.query(MarketSnapshot)
            .filter(MarketSnapshot.symbol == instrument.symbol)
            .order_by(MarketSnapshot.timestamp.desc(), MarketSnapshot.id.desc())
            .limit(2)
            .all()
        )
        current_snapshot = recent_snapshots[0] if len(recent_snapshots) >= 1 else None
        previous_snapshot = recent_snapshots[1] if len(recent_snapshots) >= 2 else None

        current_price = current_snapshot.price if current_snapshot else instrument.last_price
        current_volume = current_snapshot.volume if current_snapshot else instrument.last_volume
        previous_price = previous_snapshot.price if previous_snapshot else None

        history_points = (
            db.query(MarketSnapshot).filter(MarketSnapshot.symbol == instrument.symbol).count()
        )

        if current_price is None:
            return self._unavailable_details(instrument)

        signal_input = SignalEngineInput(
            symbol=instrument.symbol,
            current_price=current_price,
            previous_price=previous_price,
            current_volume=current_volume,
            avg_return=instrument.avg_return,
            return_variance=instrument.return_variance,
            avg_volume=instrument.avg_volume,
            week52_high=instrument.week52_high,
            week52_low=instrument.week52_low,
            history_points=history_points,
        )
        # The one and only call into the SignalEngine -- no formulas
        # duplicated here.
        analysis = analyze_attention(signal_input)

        return StockDetails(
            symbol=instrument.symbol,
            name=instrument.name,
            last_price=_to_float(instrument.last_price),
            last_price_at=instrument.last_price_at,
            last_volume=_to_float(instrument.last_volume),
            week52_high=_to_float(instrument.week52_high),
            week52_low=_to_float(instrument.week52_low),
            data_status=data_status,
            attention_score=analysis.attention_score,
            direction=analysis.direction,
            confidence=analysis.confidence,
            explanation=analysis.explanation,
            reasons=analysis.reasons,
        )

    @staticmethod
    def _unavailable_details(instrument: Instrument) -> StockDetails:
        """No price data at all yet -- don't invent a score or a status."""
        return StockDetails(
            symbol=instrument.symbol,
            name=instrument.name,
            last_price=None,
            last_price_at=None,
            last_volume=None,
            week52_high=_to_float(instrument.week52_high),
            week52_low=_to_float(instrument.week52_low),
            data_status=UNAVAILABLE,
            attention_score=None,
            direction=None,
            confidence=None,
            explanation="No market data available yet for this instrument.",
            reasons=None,
        )


# Module-level default instance, matching the `event_manager` /
# `watermark_service` shared-instance style used elsewhere in the codebase.
stock_service = StockService()
