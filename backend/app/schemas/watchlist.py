"""
Pydantic schemas for the Watchlist API.

Kept separate from the SQLAlchemy models in app/database/models.py —
these describe the API's request/response shapes, not the DB schema.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class WatchlistAddResponse(BaseModel):
    """Returned by POST /watchlist/{symbol} on success."""

    symbol: str
    added: bool
    message: str


class WatchlistItemOut(BaseModel):
    """A single row returned by GET /watchlist — watchlist entry + latest instrument info."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str | None = None
    last_price: Decimal | None = None
    last_price_at: datetime | None = None
    last_volume: Decimal | None = None
    added_at: datetime


class WatchlistRemoveResponse(BaseModel):
    """Returned by DELETE /watchlist/{symbol} on success."""

    symbol: str
    removed: bool
    message: str
