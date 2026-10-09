from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Select, delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cian_watch.db.tables import (
    DealType,
    DomclickEstimateRow,
    HiddenRow,
    OfferRow,
    OfferStatus,
    PriceHistoryRow,
    RentEstimateRow,
    WatchlistRow,
)


class CatalogScope(StrEnum):
    NEW = "new"
    PAYBACK = "payback"
    WATCHING = "watching"
    HIDDEN = "hidden"


@dataclass
class CatalogItem:
    offer: OfferRow
    estimate: DomclickEstimateRow | None
    rent: RentEstimateRow | None
    first_price: int
    watching: bool
    hidden: bool

    def price_change_percent(self) -> float:
        return (self.offer.price - self.first_price) / self.first_price * 100

    def payback_years(self) -> float | None:
        if self.rent is None:
            return None
        return self.offer.price / (self.rent.median_rent * 12)


@dataclass
class CatalogPage:
    scope: CatalogScope
    page: int
    pages: int
    total: int
    items: list[CatalogItem]


type _OfferSelect = Select[OfferRow, DomclickEstimateRow, RentEstimateRow]
type _OfferRows = Sequence[tuple[OfferRow, DomclickEstimateRow | None, RentEstimateRow | None]]


class CatalogRepository:
    """Выборки для экранов бота: списки с сортировкой и постраничной выдачей, карточка,
    личное скрытие. Всё личное считается по chat_id: скрытое одним у других остаётся."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def browse(
        self, telegram_chat_id: int, scope: CatalogScope, page: int, page_size: int
    ) -> CatalogPage:
        query = self._scope_query(telegram_chat_id=telegram_chat_id, scope=scope)
        async with self._sessions() as session:
            total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
            pages = max(1, -(-total // page_size))
            page = min(max(page, 0), pages - 1)
            rows = await session.execute(query.offset(page * page_size).limit(page_size))
            items = await self._items(
                session=session, telegram_chat_id=telegram_chat_id, rows=rows.tuples().all()
            )
        return CatalogPage(scope=scope, page=page, pages=pages, total=total, items=items)

    async def item(self, telegram_chat_id: int, offer_id: int) -> CatalogItem | None:
        async with self._sessions() as session:
            rows = await session.execute(self._base_query().where(OfferRow.id == offer_id))
            items = await self._items(session=session, telegram_chat_id=telegram_chat_id, rows=rows.all())
        return items[0] if items else None

    async def toggle_hidden(self, telegram_chat_id: int, offer_id: int, now: datetime) -> bool:
        """Скрывает объявление для пользователя (и снимает его слежение), повторный вызов
        возвращает. Возвращает состояние после."""
        async with self._sessions.begin() as session:
            removed = await session.execute(
                delete(HiddenRow)
                .where(HiddenRow.telegram_chat_id == telegram_chat_id, HiddenRow.offer_id == offer_id)
                .returning(HiddenRow.id)
            )
            if removed.first() is not None:
                return False
            await session.execute(
                delete(WatchlistRow).where(
                    WatchlistRow.telegram_chat_id == telegram_chat_id, WatchlistRow.offer_id == offer_id
                )
            )
            session.add(HiddenRow(telegram_chat_id=telegram_chat_id, offer_id=offer_id, created_at=now))
            return True

    @staticmethod
    def _first_prices_query(ids: list[int]) -> Select[int, int]:
        """Цена первой точки истории по каждому объявлению: с неё квартира вышла на Циан."""
        earliest = (
            select(PriceHistoryRow.offer_id, func.min(PriceHistoryRow.seen_at).label("seen_at"))
            .where(PriceHistoryRow.offer_id.in_(ids))
            .group_by(PriceHistoryRow.offer_id)
            .subquery()
        )
        return select(PriceHistoryRow.offer_id, PriceHistoryRow.price).join(
            earliest,
            (PriceHistoryRow.offer_id == earliest.c.offer_id)
            & (PriceHistoryRow.seen_at == earliest.c.seen_at),
        )

    @staticmethod
    def _base_query() -> _OfferSelect:
        return (
            select(OfferRow, DomclickEstimateRow, RentEstimateRow)
            .outerjoin(DomclickEstimateRow, DomclickEstimateRow.offer_id == OfferRow.id)
            .outerjoin(RentEstimateRow, RentEstimateRow.offer_id == OfferRow.id)
            .where(OfferRow.deal_type == DealType.SALE)
        )

    def _scope_query(self, telegram_chat_id: int, scope: CatalogScope) -> _OfferSelect:
        query = self._base_query()
        hidden_by_me = exists().where(
            HiddenRow.offer_id == OfferRow.id, HiddenRow.telegram_chat_id == telegram_chat_id
        )
        if scope is CatalogScope.WATCHING:
            return (
                query.join(WatchlistRow, WatchlistRow.offer_id == OfferRow.id)
                .where(WatchlistRow.telegram_chat_id == telegram_chat_id)
                .order_by(WatchlistRow.created_at.desc())
            )
        if scope is CatalogScope.HIDDEN:
            return (
                query.join(HiddenRow, HiddenRow.offer_id == OfferRow.id)
                .where(HiddenRow.telegram_chat_id == telegram_chat_id)
                .order_by(HiddenRow.created_at.desc())
            )
        query = query.where(OfferRow.status == OfferStatus.ACTIVE, ~hidden_by_me)
        if scope is CatalogScope.PAYBACK:
            return query.where(RentEstimateRow.median_rent.is_not(None)).order_by(
                (OfferRow.price / RentEstimateRow.median_rent).asc(), OfferRow.cian_created_at.desc()
            )
        return query.order_by(OfferRow.cian_created_at.desc())

    async def _items(
        self,
        session: AsyncSession,
        telegram_chat_id: int,
        rows: _OfferRows,
    ) -> list[CatalogItem]:
        ids = [offer.id for offer, _, _ in rows]
        if not ids:
            return []
        first_prices = dict((await session.execute(self._first_prices_query(ids=ids))).tuples().all())
        watching = set(
            await session.scalars(
                select(WatchlistRow.offer_id).where(
                    WatchlistRow.telegram_chat_id == telegram_chat_id, WatchlistRow.offer_id.in_(ids)
                )
            )
        )
        hidden = set(
            await session.scalars(
                select(HiddenRow.offer_id).where(
                    HiddenRow.telegram_chat_id == telegram_chat_id, HiddenRow.offer_id.in_(ids)
                )
            )
        )
        return [
            CatalogItem(
                offer=offer,
                estimate=estimate,
                rent=rent,
                first_price=first_prices.get(offer.id, offer.price),
                watching=offer.id in watching,
                hidden=offer.id in hidden,
            )
            for offer, estimate, rent in rows
        ]
