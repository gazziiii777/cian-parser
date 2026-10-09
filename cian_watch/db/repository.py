import statistics
from datetime import datetime, timedelta

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cian_watch.db.tables import (
    DealType,
    DomclickEstimateRow,
    OfferRow,
    OfferStatus,
    PriceHistoryRow,
    RentEstimateRow,
)
from cian_watch.models import DomclickEstimate, Offer, PollReport, PriceChange, RentedStats, RentEstimate


class OffersRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(
        self, offers: list[Offer], seen_at: datetime, deal_type: DealType = DealType.SALE
    ) -> PollReport:
        """Сверяет выдачу с базой в одной транзакции: новые объявления вставляет вместе с первой
        точкой истории цен, у известных обновляет last_seen_at, а при смене цены пишет новую точку.
        История растёт только на изменениях, а не на каждом опросе. Для аренды то же самое,
        цена там месячная ставка."""
        report = PollReport()
        history: list[dict[str, int | datetime]] = []
        async with self._sessions.begin() as session:
            known = await self._known_rows(session=session, ids=[offer.id for offer in offers])
            for offer in offers:
                row = known.get(offer.id, None)
                if row is None:
                    session.add(self._new_row(offer=offer, seen_at=seen_at, deal_type=deal_type))
                    history.append(
                        {
                            "offer_id": offer.id,
                            "price": offer.price,
                            "seen_at": seen_at,
                        }
                    )
                    report.new_offers.append(offer)
                    continue
                if row.price != offer.price:
                    report.price_changes.append(PriceChange(offer=offer, old_price=row.price))
                    row.price = offer.price
                    history.append(
                        {
                            "offer_id": offer.id,
                            "price": offer.price,
                            "seen_at": seen_at,
                        }
                    )
                row.last_seen_at = seen_at
                row.sweep_misses = 0
                if row.status != OfferStatus.ACTIVE:
                    report.reappeared.append(offer)
                    row.status = OfferStatus.ACTIVE
                    row.sold_at = None
                if row.raion_id is None and (raion := offer.geo.raion()) is not None:
                    row.raion_id = raion.id
                    row.raion_name = raion.name
            if history:
                await session.execute(insert(PriceHistoryRow), history)
        return report

    async def rows_by_ids(self, ids: list[int]) -> list[OfferRow]:
        if not ids:
            return []
        async with self._sessions() as session:
            rows = await session.execute(select(OfferRow).where(OfferRow.id.in_(ids)))
            return list(rows.scalars())

    async def known_ids(self, ids: list[int]) -> set[int]:
        async with self._sessions() as session:
            rows = await session.execute(select(OfferRow.id).where(OfferRow.id.in_(ids)))
            return set(rows.scalars())

    async def active_districts(self) -> list[int]:
        """Районы, где есть активные объявления продажи: аренда ходит за продажей."""
        async with self._sessions() as session:
            rows = await session.execute(
                select(OfferRow.raion_id)
                .where(
                    OfferRow.deal_type == DealType.SALE,
                    OfferRow.status == OfferStatus.ACTIVE,
                    OfferRow.raion_id.is_not(None),
                )
                .distinct()
            )
            return [raion_id for raion_id in rows.scalars() if raion_id is not None]

    async def rented_stats(self, raion_id: int, days: int, now: datetime) -> RentedStats | None:
        """Что сдалось в районе за последние days дней: объявления аренды со статусом rented,
        медиана последней ставки и медиана срока от создания у Циана до исчезновения."""
        since = now - timedelta(days=days)
        async with self._sessions() as session:
            rows = await session.execute(
                select(OfferRow.price, OfferRow.cian_created_at, OfferRow.sold_at, OfferRow.raion_name).where(
                    OfferRow.deal_type == DealType.RENT,
                    OfferRow.status == OfferStatus.RENTED,
                    OfferRow.raion_id == raion_id,
                    OfferRow.sold_at >= since,
                )
            )
            rented = rows.all()
        if not rented:
            return None
        rents = sorted(row.price for row in rented)
        exposures = sorted(
            (row.sold_at - row.cian_created_at).days for row in rented if row.sold_at is not None
        )
        return RentedStats(
            raion_name=rented[0].raion_name or str(raion_id),
            days=days,
            count=len(rented),
            median_rent=round(statistics.median(rents)),
            median_exposure_days=round(statistics.median(exposures)) if exposures else 0,
        )

    async def register_sweep(
        self,
        raion_id: int,
        deal_type: DealType,
        seen_ids: set[int],
        swept_at: datetime,
        misses_to_sell: int,
    ) -> list[int]:
        """Активные объявления района этого типа сделки, не попавшие в обход, получают промах;
        после misses_to_sell промахов подряд объявление считается ушедшим с сайта (sold или rented),
        слежка за ним прекращается, строка остаётся. Возвращает id объявлений, ушедших этим обходом."""
        async with self._sessions.begin() as session:
            rows = await session.execute(
                select(OfferRow).where(
                    OfferRow.deal_type == deal_type,
                    OfferRow.status == OfferStatus.ACTIVE,
                    OfferRow.raion_id == raion_id,
                )
            )
            gone: list[int] = []
            for row in rows.scalars():
                if row.id in seen_ids:
                    continue
                row.sweep_misses += 1
                if row.sweep_misses >= misses_to_sell:
                    row.status = deal_type.gone_status()
                    row.sold_at = swept_at
                    gone.append(row.id)
            return gone

    async def offers_without_estimate(self, offers: list[Offer], limit: int) -> list[Offer]:
        """Оставляет объявления без строки в domclick_estimates: ни оценки, ни зафиксированной
        ошибки адреса."""
        async with self._sessions() as session:
            estimated = await session.execute(
                select(DomclickEstimateRow.offer_id).where(
                    DomclickEstimateRow.offer_id.in_([offer.id for offer in offers])
                )
            )
            done = set(estimated.scalars())
        return [offer for offer in offers if offer.id not in done][:limit]

    async def save_estimate(self, estimate: DomclickEstimate, estimated_at: datetime) -> None:
        await self._upsert_estimate(
            offer_id=estimate.offer_id,
            house_guid=estimate.house_guid,
            address=estimate.address,
            market_price=estimate.market_price,
            price_m2=estimate.price_m2,
            min_price=estimate.min_price,
            max_price=estimate.max_price,
            url=estimate.url,
            error=None,
            estimated_at=estimated_at,
        )

    async def save_estimate_failure(self, offer_id: int, error: str, estimated_at: datetime) -> None:
        await self._upsert_estimate(
            offer_id=offer_id,
            house_guid=None,
            address=None,
            market_price=None,
            price_m2=None,
            min_price=None,
            max_price=None,
            url=None,
            error=error,
            estimated_at=estimated_at,
        )

    async def offers_without_rent(self, offers: list[Offer], limit: int) -> list[Offer]:
        async with self._sessions() as session:
            estimated = await session.execute(
                select(RentEstimateRow.offer_id).where(
                    RentEstimateRow.offer_id.in_([offer.id for offer in offers])
                )
            )
            done = set(estimated.scalars())
        return [offer for offer in offers if offer.id not in done][:limit]

    async def save_rent(self, rent: RentEstimate, estimated_at: datetime) -> None:
        values = {
            "offer_id": rent.offer_id,
            "scope": rent.scope,
            "sample_size": rent.sample_size,
            "median_rent": rent.median_rent,
            "min_rent": rent.min_rent,
            "max_rent": rent.max_rent,
            "estimated_at": estimated_at,
        }
        statement = pg_insert(RentEstimateRow).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[RentEstimateRow.offer_id],
            set_={key: statement.excluded[key] for key in values if key != "offer_id"},
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def _upsert_estimate(self, **values: int | str | datetime | None) -> None:
        """Одна строка на объявление: повторная оценка (повтор после сбоя, второй процесс
        на той же базе) перезаписывает предыдущую, а не падает на уникальном ключе."""
        statement = pg_insert(DomclickEstimateRow).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[DomclickEstimateRow.offer_id],
            set_={key: statement.excluded[key] for key in values if key != "offer_id"},
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def _known_rows(self, session: AsyncSession, ids: list[int]) -> dict[int, OfferRow]:
        rows = await session.execute(select(OfferRow).where(OfferRow.id.in_(ids)))
        return {row.id: row for row in rows.scalars()}

    @staticmethod
    def _new_row(offer: Offer, seen_at: datetime, deal_type: DealType) -> OfferRow:
        raion = offer.geo.raion()
        house = offer.geo.house()
        return OfferRow(
            id=offer.id,
            url=offer.url,
            address=offer.geo.user_input,
            info=offer.formatted_full_info,
            price=offer.price,
            cian_created_at=offer.creation_date,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            status=OfferStatus.ACTIVE,
            raion_id=raion.id if raion else None,
            raion_name=raion.name if raion else None,
            sweep_misses=0,
            deal_type=deal_type,
            house_id=house.id if house else None,
            rooms=offer.rooms_count,
            total_area=offer.total_area,
        )
