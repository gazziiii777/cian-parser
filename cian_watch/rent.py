import logging
import statistics
from typing import Any, ClassVar

from cian_watch.cian_search import CianSearch
from cian_watch.models import Offer, OfferFacts, RentEstimate

logger = logging.getLogger(__name__)


class RentEstimator:
    """Аренда похожих квартир по той же выдаче Циана: долгосрочная аренда, те же комнаты, площадь
    в пределах допуска. Лесенка охвата: сам дом (если в нём сдаётся хотя бы несколько похожих),
    квадрат вокруг координат, район. Берётся первая страница выдачи (28 штук), этого хватает
    для медианы."""

    RADIUS_KM: ClassVar[float] = 1.0
    MIN_SAMPLE_HOUSE: ClassVar[int] = 3
    MIN_SAMPLE_NEARBY: ClassVar[int] = 5
    AREA_TOLERANCE: ClassVar[float] = 0.2
    KM_PER_DEGREE_LAT: ClassVar[float] = 111.0
    KM_PER_DEGREE_LNG_MOSCOW: ClassVar[float] = 63.0
    STUDIO_ROOM_CODE: ClassVar[int] = 9
    NOT_DAILY: ClassVar[str] = "!1"
    REGION_MOSCOW: ClassVar[int] = 1
    SCOPE_HOUSE: ClassVar[str] = "дом"
    SCOPE_NEARBY: ClassVar[str] = "1 км"

    def __init__(self, cian: CianSearch) -> None:
        self._cian = cian

    async def estimate(self, offer: OfferFacts) -> RentEstimate | None:
        """None, если ни в доме, ни рядом, ни в районе нет похожих квартир в аренду."""
        candidates: list[tuple[str, dict[str, Any] | None, int]] = [
            (self.SCOPE_HOUSE, self._house_query(offer=offer), self.MIN_SAMPLE_HOUSE),
            (self.SCOPE_NEARBY, self._nearby_query(offer=offer), self.MIN_SAMPLE_NEARBY),
            (self._district_scope(offer=offer), self._district_query(offer=offer), 1),
        ]
        comparables: list[Offer] = []
        used_scope = ""
        for scope, query, min_sample in candidates:
            if query is None:
                continue
            comparables = await self._cian.search(json_query=query)
            used_scope = scope
            if len(comparables) >= min_sample:
                break
        if not comparables:
            return None
        rents = sorted(comparable.price for comparable in comparables)
        return RentEstimate(
            offer_id=offer.id,
            scope=used_scope,
            sample_size=len(rents),
            median_rent=round(statistics.median(rents)),
            min_rent=rents[0],
            max_rent=rents[-1],
        )

    def _house_query(self, offer: OfferFacts) -> dict[str, Any] | None:
        if offer.house_id is None:
            return None
        house_geo = {"type": "geo", "value": [{"type": "house", "id": offer.house_id}]}
        return {**self.base_query(offer=offer), "geo": house_geo}

    def _nearby_query(self, offer: OfferFacts) -> dict[str, Any] | None:
        if offer.lat is None or offer.lng is None:
            return None
        lat, lng = offer.lat, offer.lng
        d_lat = self.RADIUS_KM / self.KM_PER_DEGREE_LAT
        d_lng = self.RADIUS_KM / self.KM_PER_DEGREE_LNG_MOSCOW
        corners = [
            (lng - d_lng, lat - d_lat),
            (lng + d_lng, lat - d_lat),
            (lng + d_lng, lat + d_lat),
            (lng - d_lng, lat + d_lat),
            (lng - d_lng, lat - d_lat),
        ]
        polygon = {"type": "polygon", "name": "nearby", "coordinates": [[str(x), str(y)] for x, y in corners]}
        return {**self.base_query(offer=offer), "geo": {"type": "geo", "value": [polygon]}}

    def _district_query(self, offer: OfferFacts) -> dict[str, Any] | None:
        if offer.raion_id is None:
            return None
        return CianSearch.with_district(self.base_query(offer=offer), offer.raion_id)

    def _district_scope(self, offer: OfferFacts) -> str:
        return f"район {offer.raion_name}" if offer.raion_name else "район"

    def base_query(self, offer: OfferFacts) -> dict[str, Any]:
        """Долгосрочная аренда с теми же комнатами и площадью ±допуск. Цифры в полигоне
        отправляются строками там, где так делает сам сайт: иначе API полигон игнорирует."""
        rooms = offer.rooms if offer.rooms is not None else self.STUDIO_ROOM_CODE
        return {
            **self.district_base_query(),
            "room": {"type": "terms", "value": [rooms]},
            "total_area": {
                "type": "range",
                "value": {
                    "gte": round(offer.total_area * (1 - self.AREA_TOLERANCE)),
                    "lte": round(offer.total_area * (1 + self.AREA_TOLERANCE)),
                },
            },
        }

    @classmethod
    def district_base_query(cls) -> dict[str, Any]:
        """Вся долгосрочная аренда квартир в Москве без ограничений по комнатам и площади:
        основа для районного обхода, где нужна полная картина."""
        return {
            "_type": "flatrent",
            "engine_version": {"type": "term", "value": 2},
            "region": {"type": "terms", "value": [cls.REGION_MOSCOW]},
            "for_day": {"type": "term", "value": cls.NOT_DAILY},
        }
