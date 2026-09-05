"""Development-only deterministic scenarios for the hackathon demo."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import Instrument, WatchlistItem
from app.database.session import get_db
from app.routes.watchlist import DEMO_USER_ID
from app.services.market_data import MarketDataPoint, MarketDataProvider
from app.workers.market_worker import MarketMonitoringWorker

router = APIRouter(prefix="/demo", tags=["demo"])

ScenarioName = Literal[
    "normal",
    "price_shock",
    "volume_anomaly",
    "relative_performance",
    "stale_data",
]


class DemoScenarioResponse(BaseModel):
    scenario: ScenarioName
    symbol: str
    success: bool
    attention_score: float | None
    direction: str | None
    explanation: str | None
    data_status: str | None
    event_created: bool
    error: str | None


class OneShotProvider(MarketDataProvider):
    def __init__(self, point: MarketDataPoint):
        self._point = point

    async def get_latest(self, symbol: str) -> MarketDataPoint:
        if symbol != self._point.symbol:
            raise KeyError(f"No demo point configured for {symbol}")
        return self._point


def _scenario_point(
    scenario: ScenarioName, instrument: Instrument
) -> tuple[MarketDataPoint, float | None]:
    now = datetime.now(timezone.utc)
    price = instrument.last_price or Decimal("100")
    average_volume = instrument.avg_volume or instrument.last_volume or Decimal("1000000")
    benchmark_return = None
    data_status = "fresh"
    timestamp = now

    if scenario == "price_shock":
        price *= Decimal("1.15")
        volume = int(average_volume * Decimal("5"))
        benchmark_return = 0.0
    elif scenario == "volume_anomaly":
        volume = int(average_volume * Decimal("6"))
    elif scenario == "relative_performance":
        volume = int(average_volume)
        benchmark_return = -0.05
    elif scenario == "stale_data":
        price *= Decimal("1.15")
        volume = int(average_volume * Decimal("5"))
        data_status = "stale"
        timestamp = now - timedelta(minutes=30)
    else:
        volume = int(average_volume)

    point = MarketDataPoint(
        symbol=instrument.symbol,
        name=instrument.name or instrument.symbol,
        price=price.quantize(Decimal("0.01")),
        volume=volume,
        timestamp=timestamp,
        source="deterministic-demo",
        data_status=data_status,
    )
    return point, benchmark_return


@router.post("/scenarios/{scenario}/{symbol}", response_model=DemoScenarioResponse)
async def run_scenario(
    scenario: ScenarioName, symbol: str, db: Session = Depends(get_db)
):
    if get_settings().environment == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    symbol = symbol.strip().upper()
    if db.get(WatchlistItem, (DEMO_USER_ID, symbol)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{symbol}' is not in the watchlist",
        )

    instrument = db.get(Instrument, symbol)
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instrument '{symbol}' does not exist",
        )

    point, benchmark_return = _scenario_point(scenario, instrument)
    worker = MarketMonitoringWorker(market_data_provider=OneShotProvider(point))
    result = await worker.process_symbol(symbol, db=db, benchmark_return=benchmark_return)
    if result.success:
        db.commit()
    else:
        db.rollback()

    analysis = result.attention_analysis
    return DemoScenarioResponse(
        scenario=scenario,
        symbol=symbol,
        success=result.success,
        attention_score=analysis.attention_score if analysis else None,
        direction=analysis.direction if analysis else None,
        explanation=analysis.explanation if analysis else None,
        data_status=result.market_data.data_status if result.market_data else None,
        event_created=result.significance_event is not None,
        error=result.error,
    )