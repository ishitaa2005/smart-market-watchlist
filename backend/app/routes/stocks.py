"""
Stock Details API.

    GET /stocks/{symbol}  - current known state of one instrument:
                             latest price/volume, 52-week range, and an
                             attention analysis (persisted if an active
                             one exists, else computed on the fly from
                             already-persisted history) -- read-only.

All lookup/analysis logic lives in app.services.stock_service. This route
never touches EventManager, WatermarkService, WatchlistItem, or the
market monitoring worker, and never writes to the database.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.stock import StockDetailsOut
from app.services.stock_service import stock_service

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/{symbol}", response_model=StockDetailsOut)
def get_stock_details(symbol: str, db: Session = Depends(get_db)):
    """
    Returns the current known state of `symbol`. Case-insensitive --
    /stocks/tcs and /stocks/TCS behave identically. 404s if the symbol
    isn't a recognized instrument.
    """
    details = stock_service.get_stock_details(db, symbol)
    if details is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instrument '{symbol.strip().upper()}' does not exist",
        )
    return details
