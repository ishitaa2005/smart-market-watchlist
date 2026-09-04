"""
Watchlist CRUD routes.

No auth yet — every request acts on behalf of a single fixed demo user
(DEMO_USER_ID). The demo user is created automatically on first add.

No market-data fetching and no significance/event logic here — this route
only manages which instruments are on the watchlist and reads whatever
instrument snapshot data already exists in the instruments table.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database.models import Instrument, User, WatchlistItem
from app.database.session import get_db
from app.schemas.watchlist import (
    WatchlistAddResponse,
    WatchlistItemOut,
    WatchlistRemoveResponse,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

# Fixed demo user until real auth exists.
DEMO_USER_ID = "demo-user"


def _get_or_create_demo_user(db: Session) -> User:
    user = db.get(User, DEMO_USER_ID)
    if user is None:
        user = User(id=DEMO_USER_ID)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.post(
    "/{symbol}",
    response_model=WatchlistAddResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_to_watchlist(symbol: str, db: Session = Depends(get_db)):
    symbol = symbol.strip().upper()

    instrument = db.get(Instrument, symbol)
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instrument '{symbol}' does not exist",
        )

    user = _get_or_create_demo_user(db)

    existing = db.get(WatchlistItem, (user.id, symbol))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{symbol}' is already in the watchlist",
        )

    item = WatchlistItem(user_id=user.id, symbol=symbol)
    db.add(item)
    db.commit()

    return WatchlistAddResponse(
        symbol=symbol, added=True, message=f"'{symbol}' added to watchlist"
    )


@router.get("", response_model=list[WatchlistItemOut])
def get_watchlist(db: Session = Depends(get_db)):
    items = (
        db.query(WatchlistItem)
        .options(joinedload(WatchlistItem.instrument))
        .filter(WatchlistItem.user_id == DEMO_USER_ID)
        .order_by(WatchlistItem.added_at.desc())
        .all()
    )

    return [
        WatchlistItemOut(
            symbol=item.symbol,
            name=item.instrument.name,
            last_price=item.instrument.last_price,
            last_price_at=item.instrument.last_price_at,
            last_volume=item.instrument.last_volume,
            added_at=item.added_at,
        )
        for item in items
    ]


@router.delete("/{symbol}", response_model=WatchlistRemoveResponse)
def remove_from_watchlist(symbol: str, db: Session = Depends(get_db)):
    symbol = symbol.strip().upper()

    item = db.get(WatchlistItem, (DEMO_USER_ID, symbol))
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{symbol}' is not in the watchlist",
        )

    db.delete(item)
    db.commit()

    return WatchlistRemoveResponse(
        symbol=symbol, removed=True, message=f"'{symbol}' removed from watchlist"
    )
