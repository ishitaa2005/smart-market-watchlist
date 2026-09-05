"""Idempotently seed the hackathon demo user, instruments, watchlist, and events."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.database.models import Instrument, SignificanceEvent, User, WatchlistItem
from app.database.session import SessionLocal
from app.routes.watchlist import DEMO_USER_ID
from app.services.market_data import get_supported_instruments


DEMO_EVENTS = {
    "RELIANCE": {
        "minutes_ago": 8,
        "score": Decimal("86.4"),
        "direction": "UP",
        "price": Decimal("2968.40"),
        "reasons": [
            {
                "code": "PRICE_ANOMALY",
                "message": "Price movement was significantly larger than its recent baseline.",
                "value": 3.7,
            },
            {
                "code": "VOLUME_SURGE",
                "message": "Trading volume accelerated while the price moved higher.",
                "value": 2.4,
            },
        ],
        "confidence": "high",
        "data_status": "fresh",
    },
    "INFY": {
        "minutes_ago": 24,
        "score": Decimal("58.2"),
        "direction": "DOWN",
        "price": Decimal("1526.75"),
        "reasons": [
            {
                "code": "RELATIVE_WEAKNESS",
                "message": "INFY weakened while the broader watchlist remained comparatively stable.",
                "value": -1.8,
            }
        ],
        "confidence": "medium",
        "data_status": "delayed",
    },
    "HDFCBANK": {
        "minutes_ago": 51,
        "score": Decimal("42.0"),
        "direction": "NEUTRAL",
        "price": Decimal("1654.10"),
        "reasons": [
            {
                "code": "VOLUME_ANOMALY",
                "message": "Trading activity increased without a clear price direction.",
                "value": 1.6,
            }
        ],
        "confidence": "medium",
        "data_status": "delayed",
    },
}


def seed_demo() -> None:
    db = SessionLocal()
    try:
        if db.get(User, DEMO_USER_ID) is None:
            db.add(User(id=DEMO_USER_ID))

        now = datetime.now(timezone.utc)
        seeded_symbols: list[str] = []

        for item in get_supported_instruments():
            symbol = item["symbol"]
            price = item["price"]
            volume = item["volume"]
            instrument = db.get(Instrument, symbol)

            if instrument is None:
                instrument = Instrument(
                    symbol=symbol,
                    name=item["name"],
                    last_price=price,
                    last_price_at=now,
                    last_volume=volume,
                    avg_return=Decimal("0"),
                    return_variance=Decimal("0.000016"),
                    avg_volume=volume,
                    week52_high=(price * Decimal("1.20")).quantize(Decimal("0.01")),
                    week52_low=(price * Decimal("0.80")).quantize(Decimal("0.01")),
                )
                db.add(instrument)
                db.flush()
            else:
                instrument.name = instrument.name or item["name"]
                instrument.last_price = instrument.last_price or price
                instrument.last_price_at = instrument.last_price_at or now
                instrument.last_volume = instrument.last_volume or volume
                instrument.avg_return = instrument.avg_return if instrument.avg_return is not None else Decimal("0")
                instrument.return_variance = instrument.return_variance or Decimal("0.000016")
                instrument.avg_volume = instrument.avg_volume or volume
                instrument.week52_high = instrument.week52_high or (price * Decimal("1.20")).quantize(Decimal("0.01"))
                instrument.week52_low = instrument.week52_low or (price * Decimal("0.80")).quantize(Decimal("0.01"))

            if db.get(WatchlistItem, (DEMO_USER_ID, symbol)) is None:
                db.add(WatchlistItem(user_id=DEMO_USER_ID, symbol=symbol))
            seeded_symbols.append(symbol)

        for symbol, event_data in DEMO_EVENTS.items():
            has_event = db.query(SignificanceEvent.id).filter(SignificanceEvent.symbol == symbol).first()
            if has_event is None:
                db.add(
                    SignificanceEvent(
                        symbol=symbol,
                        occurred_at=now - timedelta(minutes=event_data["minutes_ago"]),
                        score=event_data["score"],
                        direction=event_data["direction"],
                        price=event_data["price"],
                        reasons=event_data["reasons"],
                        data_confidence=event_data["confidence"],
                        data_status=event_data["data_status"],
                        status="active",
                    )
                )

        db.commit()
        print(f"Demo watchlist ready: {', '.join(seeded_symbols)}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()