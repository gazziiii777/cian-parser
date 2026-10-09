import logging
import time
from http import HTTPStatus
from typing import ClassVar
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page, Route
from pydantic import BaseModel, ValidationError

from cian_watch.browser import BrowserHost
from cian_watch.exceptions import DomclickAddressNotFound, DomclickError, DomclickNoPrediction
from cian_watch.models import DomclickEstimate, DomclickPredictResponse, DomclickSuggestResponse, OfferFacts
from config.settings import Settings

logger = logging.getLogger(__name__)


class DomclickEstimator:
    """Оценка квартиры на price.domclick.ru через тот же мобильный прокси, что и Циан:
    с домашнего IP Qrator после пачки запросов перестаёт отвечать совсем. Без браузера API отдаёт 401,
    но после одного открытия страницы куки контекста пускают в JSON API, и дальше оценка
    это два GET: подсказка адреса даёт guid дома, predict по guid и параметрам даёт цену."""

    HOME_URL: ClassVar[str] = "https://price.domclick.ru/"
    SUGGEST_URL: ClassVar[str] = "https://price-bff.domclick.ru/api/v2/geo/suggest/"
    PREDICT_URL: ClassVar[str] = "https://price-bff.domclick.ru/api/v2/price/predict/"
    ADDRESS_INPUT: ClassVar[str] = "#full_address_input"
    ALLOWED_HOSTS: ClassVar[frozenset[str]] = frozenset(
        {"price.domclick.ru", "price-bff.domclick.ru", "statics.dmclk.ru"}
    )
    REPAIR_QUALITY: ClassVar[int] = 2
    COUNTRY_PREFIX: ClassVar[str] = "Россия, "
    RUBLES_IN_MILLION: ClassVar[int] = 1_000_000
    REQUEST_TIMEOUT_MS: ClassVar[int] = 10_000
    PAGE_OPEN_TIMEOUT_MS: ClassVar[int] = 30_000

    def __init__(self, host: BrowserHost, settings: Settings) -> None:
        self._host = host
        self._settings = settings
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._generation = -1

    async def ensure_open(self) -> None:
        if self._context is None or self._generation != self._host.generation:
            await self.open()

    async def open(self) -> None:
        """Открывает главную оценки и ждёт форму: к этому моменту проверка Qrator пройдена
        и куки контекста годятся для API."""
        self._context = await self._host.new_context(use_proxy=True)
        self._generation = self._host.generation
        await self._context.route("**/*", self._drop_third_party)
        self._page = await self._context.new_page()
        started = time.monotonic()
        logger.info("opening domclick page through proxy, up to %ds", self.PAGE_OPEN_TIMEOUT_MS // 1000)
        await self._page.goto(self.HOME_URL, wait_until="domcontentloaded", timeout=self.PAGE_OPEN_TIMEOUT_MS)
        await self._page.wait_for_selector(self.ADDRESS_INPUT, timeout=self.PAGE_OPEN_TIMEOUT_MS)
        logger.info("domclick context opened in %.1fs", time.monotonic() - started)

    async def _drop_third_party(self, route: Route) -> None:
        """Страница оценки тянет аналитику Сбера (visor.sbrf.ru, clckdm, api.domclick.ru), прокси
        её режет и засоряет лог. Для проверки Qrator и API хватает трёх хостов, остальное
        обрываем ещё в браузере."""
        host = route.request.url.split("/")[2]
        if host in self.ALLOWED_HOSTS:
            await route.continue_()
        else:
            await route.abort()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        self._context = None
        self._page = None

    async def estimate(self, offer: OfferFacts) -> DomclickEstimate:
        """Подсказка адреса, затем predict. Если Qrator перестал пускать (401), страница
        открывается заново один раз и запрос повторяется."""
        if offer.address is None:
            raise DomclickAddressNotFound(f"offer {offer.id} has no address")
        await self.ensure_open()
        query = offer.address.removeprefix(self.COUNTRY_PREFIX)
        suggest = await self._get_json(
            url=f"{self.SUGGEST_URL}?{urlencode({'query': query})}",
            model=DomclickSuggestResponse,
        )
        house = next((suggestion for suggestion in suggest.answer if suggestion.guid), None)
        if house is None or house.guid is None:
            variants = [suggestion.display_name for suggestion in suggest.answer[:3]]
            raise DomclickAddressNotFound(f"no domclick house for {query!r}, suggestions: {variants}")
        params = self._predict_params(offer=offer, house_guid=house.guid)
        predict = await self._get_json(
            url=f"{self.PREDICT_URL}?{urlencode(params)}",
            model=DomclickPredictResponse,
        )
        if predict.answer is None:
            raise DomclickNoPrediction(f"domclick can not predict house {house.display_name!r}")
        return DomclickEstimate(
            offer_id=offer.id,
            house_guid=house.guid,
            address=house.display_name,
            market_price=self._to_rubles(predict.answer.market_price),
            price_m2=self._to_rubles(predict.answer.market_price_m2),
            min_price=self._to_rubles(predict.answer.min_market_price),
            max_price=self._to_rubles(predict.answer.max_market_price),
            url=f"{self.HOME_URL}?{urlencode(params)}",
        )

    def _predict_params(self, offer: OfferFacts, house_guid: str) -> dict[str, str | int | float]:
        params: dict[str, str | int | float] = {
            "guid": house_guid,
            "comm_sq": offer.total_area,
            "quality": self.REPAIR_QUALITY,
        }
        if offer.rooms is not None:
            params["rooms"] = offer.rooms
        if offer.floor is not None:
            params["floor"] = offer.floor
        return params

    async def _get_json[T: BaseModel](self, url: str, model: type[T]) -> T:
        if self._context is None:
            raise RuntimeError("domclick context is not opened")
        response = await self._context.request.get(url, timeout=self.REQUEST_TIMEOUT_MS)
        if response.status == HTTPStatus.UNAUTHORIZED:
            logger.warning("domclick challenge expired, reopening the page")
            await self.close()
            await self.open()
            response = await self._context.request.get(url, timeout=self.REQUEST_TIMEOUT_MS)
        if response.status != HTTPStatus.OK:
            raise DomclickError(f"domclick answered {response.status} for {url}")
        try:
            return model.model_validate_json(await response.body())
        except ValidationError as exc:
            raise DomclickError(f"domclick response has unexpected shape: {exc}") from exc

    def _to_rubles(self, millions: float) -> int:
        return round(millions * self.RUBLES_IN_MILLION)
