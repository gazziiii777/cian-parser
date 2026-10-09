from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from cian_watch.models.offer_facts import OfferFacts


class OfferCoordinates(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat: float
    lng: float


class OfferAddressPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = Field(description="Уровень адреса у Циана: location, okrug, raion, metro, street, house")
    id: int = Field(description="ID этого уровня в гео-справочнике Циана, годится в jsonQuery")
    name: str


class OfferGeo(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    user_input: str | None = Field(default=None, description="Адрес, как его ввёл автор объявления")
    coordinates: OfferCoordinates | None = Field(
        default=None, description="Точка дома, у части объявлений скрыта"
    )
    address: list[OfferAddressPart] = Field(
        default_factory=list, description="Цепочка адреса от города до дома"
    )

    def raion(self) -> OfferAddressPart | None:
        return next((part for part in self.address if part.type == "raion"), None)

    def house(self) -> OfferAddressPart | None:
        return next((part for part in self.address if part.type == "house"), None)


class OfferBargainTerms(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    price: int = Field(description="Цена в рублях целым числом")


class Offer(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    CIAN_TIMEZONE: ClassVar[ZoneInfo] = ZoneInfo("Europe/Moscow")

    id: int = Field(description="ID объявления на Циане")
    full_url: str = Field(description="Ссылка на объявление вместе с трекинговым context в query")
    formatted_full_price: str = Field(description="Цена в готовом для показа виде, как на карточке")
    formatted_full_info: str = Field(description="Комнаты, площадь и этаж одной строкой, как на карточке")
    creation_date: datetime = Field(
        description="Когда объявление создано по данным Циана; сайт отдаёт московское время без зоны"
    )
    total_area: float = Field(description="Общая площадь в м², Циан отдаёт строкой вида '112.0'")
    rooms_count: int | None = Field(
        default=None, description="Число комнат, у студий и свободных планировок нет"
    )
    floor_number: int | None = Field(default=None, description="Этаж квартиры, у части объявлений не указан")
    geo: OfferGeo
    bargain_terms: OfferBargainTerms

    @field_validator("creation_date")
    @classmethod
    def attach_cian_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=cls.CIAN_TIMEZONE)
        return value

    @property
    def url(self) -> str:
        return self.full_url.split("?", maxsplit=1)[0]

    @property
    def address(self) -> str:
        return self.geo.user_input or "address hidden"

    @property
    def price(self) -> int:
        return self.bargain_terms.price

    def facts(self) -> OfferFacts:
        raion = self.geo.raion()
        house = self.geo.house()
        return OfferFacts(
            id=self.id,
            url=self.url,
            price=self.price,
            address=self.geo.user_input,
            total_area=self.total_area,
            rooms=self.rooms_count,
            floor=self.floor_number,
            house_id=house.id if house else None,
            lat=self.geo.coordinates.lat if self.geo.coordinates else None,
            lng=self.geo.coordinates.lng if self.geo.coordinates else None,
            raion_id=raion.id if raion else None,
            raion_name=raion.name if raion else None,
        )
