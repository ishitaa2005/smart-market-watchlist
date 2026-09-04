"""
SQLAlchemy models for the Smart Market Watchlist schema.

Six tables:
    users              - accounts
    instruments         - tracked symbols + rolling stats used by the
                          significance engine (computed in a later step)
    watchlist_items     - which users watch which instruments
    market_snapshots     - raw price/volume ticks as ingested (append-only)
    significance_events - detected "meaningful change" events (append-only)
    user_watermarks      - per-user, per-symbol "last seen event" pointer,
                          used to compute "what changed since you last checked"

No business logic, no signal-scoring logic, no routes here — models only.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric)
    last_price_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_volume: Mapped[Decimal | None] = mapped_column(Numeric)

    # rolling stats used by the significance engine (computed in a later step)
    avg_return: Mapped[Decimal | None] = mapped_column(Numeric)
    return_variance: Mapped[Decimal | None] = mapped_column(Numeric)
    avg_volume: Mapped[Decimal | None] = mapped_column(Numeric)

    week52_high: Mapped[Decimal | None] = mapped_column(Numeric)
    week52_low: Mapped[Decimal | None] = mapped_column(Numeric)

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(back_populates="instrument")
    snapshots: Mapped[list["MarketSnapshot"]] = relationship(back_populates="instrument")
    significance_events: Mapped[list["SignificanceEvent"]] = relationship(
        back_populates="instrument"
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="watchlist_items")
    instrument: Mapped["Instrument"] = relationship(back_populates="watchlist_items")


class MarketSnapshot(Base):
    """Raw ingested price/volume ticks — append-only, one row per tick."""

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"))
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    volume: Mapped[Decimal | None] = mapped_column(Numeric)
    timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    source: Mapped[str | None] = mapped_column(Text)
    data_status: Mapped[str | None] = mapped_column(Text)

    instrument: Mapped["Instrument"] = relationship(back_populates="snapshots")


class SignificanceEvent(Base):
    """Detected 'meaningful change' events — append-only, one row per event."""

    __tablename__ = "significance_events"
    __table_args__ = (
        Index("ix_significance_events_symbol_occurred_at", "symbol", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"))
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric)
    direction: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    reasons: Mapped[dict | None] = mapped_column(JSONB)
    data_confidence: Mapped[str | None] = mapped_column(Text)
    data_status: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)

    instrument: Mapped["Instrument"] = relationship(back_populates="significance_events")


class UserWatermark(Base):
    """Per-user, per-symbol 'last seen event' pointer for change diffing."""

    __tablename__ = "user_watermarks"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seen_event_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
