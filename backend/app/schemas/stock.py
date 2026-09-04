"""
Pydantic schema for the Stock Details API (GET /stocks/{symbol}).

Kept separate from the SQLAlchemy models and from the StockDetails DTO in
app/services/stock_service.py -- this describes the API's response shape
only.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class StockDetailsOut(BaseModel):
    """Current known state of one instrument, as returned by GET /stocks/{symbol}."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None

    last_price: Optional[float] = None
    last_price_at: Optional[datetime] = None
    last_volume: Optional[float] = None

    week52_high: Optional[float] = None
    week52_low: Optional[float] = None

    data_status: str  # fresh | delayed | stale | unavailable

    attention_score: Optional[float] = None
    direction: Optional[str] = None
    confidence: Optional[str] = None
    explanation: Optional[str] = None
    reasons: Optional[Any] = None
