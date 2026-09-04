"""
"Changes since last check" API.

    GET  /watchlist/changes        - unseen significance events for the
                                      demo user's watchlist (read-only:
                                      never touches the watermark)
    POST /watchlist/{symbol}/ack   - advance the demo user's watermark
                                      for one symbol to its latest
                                      significance event

All watermark reads/writes go through app.services.watermark -- this
module is just the FastAPI-facing wrapper (request handling, 404s,
response shaping). No SignalEngine/EventManager/watermark business logic
lives here.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.models import WatchlistItem
from app.database.session import get_db
from app.routes.watchlist import DEMO_USER_ID
from app.schemas.event import AckResponse, SignificanceEventOut
from app.services.watermark import watermark_service

router = APIRouter(prefix="/watchlist", tags=["changes"])


@router.get("/changes", response_model=list[SignificanceEventOut])
def get_changes(db: Session = Depends(get_db)):
    """
    Everything meaningful that happened on the demo user's watchlist
    since their last ack, newest first. Read-only -- does not move any
    watermark.
    """
    changes = watermark_service.get_changes(db, DEMO_USER_ID)
    return [SignificanceEventOut.model_validate(change) for change in changes]


@router.post("/{symbol}/ack", response_model=AckResponse)
def ack_symbol(symbol: str, db: Session = Depends(get_db)):
    """
    Mark `symbol` as seen up through its latest significance event.

    404s if `symbol` isn't on the demo user's watchlist. Idempotent, and
    never moves the watermark backwards (see WatermarkService.acknowledge).
    """
    symbol = symbol.strip().upper()

    item = db.get(WatchlistItem, (DEMO_USER_ID, symbol))
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{symbol}' is not in the watchlist",
        )

    last_seen_event_id = watermark_service.acknowledge(db, DEMO_USER_ID, symbol)
    db.commit()

    message = (
        f"'{symbol}' marked as seen up to event {last_seen_event_id}"
        if last_seen_event_id
        else f"'{symbol}' has no events yet -- watermark initialized"
    )

    return AckResponse(
        symbol=symbol,
        acknowledged=True,
        last_seen_event_id=last_seen_event_id,
        message=message,
    )