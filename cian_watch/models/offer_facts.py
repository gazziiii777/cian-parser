from dataclasses import dataclass


@dataclass
class OfferFacts:
    """Что нужно оценщикам от объявления, одинаково для свежей выдачи Циана и для строки
    в базе: Домклику адрес, площадь, комнаты и этаж, аренде рядом дом, координаты и район."""

    id: int
    url: str
    price: int
    address: str | None
    total_area: float
    rooms: int | None
    floor: int | None
    house_id: int | None
    lat: float | None
    lng: float | None
    raion_id: int | None
    raion_name: str | None
