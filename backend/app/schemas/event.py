"""
Pydantic schemas for the "changes since last check" API
(GET /watchlist/changes, POST /watchlist/{symbol}/ack).

Kept separate from the SQLAlchemy models in app/database/models.py and
from the SymbolChange DTO in app/services/watermark.py -- this describes
the API's response shape only.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class SignificanceEventOut(BaseModel):
    """One unseen significance event, as returned by GET /watchlist/changes."""

    model_config = ConfigDict(from_attributes=True)

    event_id: int
    symbol: str
    score: Optional[float] = None
    direction: Optional[str] = None
    price: Optional[float] = None
    occurred_at: datetime
    reasons: Optional[Any] = None
    explanation: Optional[str] = None
    confidence: Optional[str] = None
    data_status: Optional[str] = None
    status: Optional[str] = None


class AckResponse(BaseModel):
    """Returned by POST /watchlist/{symbol}/ack on success."""

    symbol: str
    acknowledged: bool
    last_seen_event_id: int
    message: str