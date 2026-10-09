from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OfferStatus(StrEnum):
    ACTIVE = "active"
    SOLD = "sold"
    RENTED = "rented"


class DealType(StrEnum):
    SALE = "sale"
    RENT = "rent"

    def gone_status(self) -> OfferStatus:
        return OfferStatus.SOLD if self is DealType.SALE else OfferStatus.RENTED


class OfferRow(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    url: Mapped[str] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    info: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(BigInteger)
    cian_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[OfferStatus] = mapped_column(Text, default=OfferStatus.ACTIVE)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raion_id: Mapped[int | None] = mapped_column(Integer)
    raion_name: Mapped[str | None] = mapped_column(Text)
    sweep_misses: Mapped[int] = mapped_column(Integer, default=0)
    deal_type: Mapped[DealType] = mapped_column(Text, default=DealType.SALE)
    house_id: Mapped[int | None] = mapped_column(BigInteger)
    rooms: Mapped[int | None] = mapped_column(Integer)
    total_area: Mapped[float | None] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)


class PriceHistoryRow(Base):
    __tablename__ = "price_history"
    __table_args__ = (Index("ix_price_history_offer_seen", "offer_id", "seen_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    offer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("offers.id", ondelete="CASCADE"))
    price: Mapped[int] = mapped_column(BigInteger)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DomclickEstimateRow(Base):
    __tablename__ = "domclick_estimates"

    offer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    house_guid: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    market_price: Mapped[int | None] = mapped_column(BigInteger)
    price_m2: Mapped[int | None] = mapped_column(BigInteger)
    min_price: Mapped[int | None] = mapped_column(BigInteger)
    max_price: Mapped[int | None] = mapped_column(BigInteger)
    url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    estimated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RentEstimateRow(Base):
    __tablename__ = "rent_estimates"

    offer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    scope: Mapped[str] = mapped_column(Text)
    sample_size: Mapped[int] = mapped_column(Integer)
    median_rent: Mapped[int] = mapped_column(BigInteger)
    min_rent: Mapped[int] = mapped_column(BigInteger)
    max_rent: Mapped[int] = mapped_column(BigInteger)
    estimated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WatchlistRow(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "offer_id", name="uq_watchlist_chat_offer"),
        Index("ix_watchlist_offer", "offer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_chat_id", ondelete="CASCADE")
    )
    offer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("offers.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HiddenRow(Base):
    __tablename__ = "hidden"
    __table_args__ = (UniqueConstraint("telegram_chat_id", "offer_id", name="uq_hidden_chat_offer"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_chat_id", ondelete="CASCADE")
    )
    offer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("offers.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
