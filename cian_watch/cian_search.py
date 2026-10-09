import asyncio
import json
import logging
import time
from datetime import timedelta
from http import HTTPStatus
from typing import Any, ClassVar

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cian_watch.browser import BrowserHost
from cian_watch.exceptions import BlockedError
from cian_watch.models import Offer, PageFetchResult
from cian_watch.serp import parse_api_offers, parse_initial_state
from config.settings import Settings

logger = logging.getLogger(__name__)


class CianSearch:
    """Выдача Циана без рендера страниц. Вкладка один раз открывает выдачу по URL из настроек:
    это даёт сессию, отпечаток и точный jsonQuery под фильтр. Дальше страницы 1..N берутся
    POST-запросами к внутреннему API из этой вкладки: антибот пускает только отпечаток
    настоящего Firefox, куки сами по себе не помогают."""

    CARD_SELECTOR: ClassVar[str] = '[data-name="CardComponent"]'
    CAPTCHA_URL_MARKER: ClassVar[str] = "captcha"
    API_URL: ClassVar[str] = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
    PAGE_SIZE: ClassVar[int] = 28
    FETCH_SCRIPT: ClassVar[str] = """async ([url, body]) => {
        const response = await fetch(url, {
            method: "POST",
            credentials: "include",
            headers: {"content-type": "application/json"},
            body,
        });
        return {status: response.status, text: await response.text()};
    }"""

    def __init__(self, host: BrowserHost, settings: Settings, role: str, session_lock: asyncio.Lock) -> None:
        self._host = host
        self._settings = settings
        self._role = role
        self._session_lock = session_lock
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._json_query: dict[str, Any] | None = None
        self._generation = -1

    async def ensure_open(self) -> None:
        """Контекст открывается при первом обращении и заново после перезапуска браузера
        (поколение хоста ушло вперёд): старый контекст к тому моменту уже мёртв."""
        if self._context is None or self._generation != self._host.generation:
            self._context = await self._host.new_context(use_proxy=True)
            self._page = await self._context.new_page()
            self._json_query = None
            self._generation = self._host.generation
            logger.info("cian context opened for %s", self._role)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        self._context = None
        self._page = None
        self._json_query = None

    async def fetch_page(self, page_number: int) -> list[Offer]:
        """Страница ленты по фильтру из настроек, объявления по дате добавления."""
        await self.ensure_open()
        if self._json_query is None:
            await self._establish_session()
        query = {**(self._json_query or {}), "page": {"type": "term", "value": page_number}}
        started = time.monotonic()
        offers = await self.search(json_query=query)
        logger.info(
            "cian feed page %d: %d offers in %.1fs", page_number, len(offers), time.monotonic() - started
        )
        return offers

    async def fetch_district(self, raion_id: int, max_pages: int) -> list[Offer]:
        """Все объявления района по фильтру из настроек (продажа)."""
        await self.ensure_open()
        if self._json_query is None:
            await self._establish_session()
        return await self.fetch_all(
            json_query=self.with_district(self._json_query or {}, raion_id), max_pages=max_pages
        )

    async def fetch_all(self, json_query: dict[str, Any], max_pages: int) -> list[Offer]:
        """Страницы запроса идут, пока они полные: так каждое объявление по запросу попадает
        в кадр, и его отсутствие значит, что оно ушло с сайта, а не с первых страниц ленты."""
        offers: dict[int, Offer] = {}
        for page_number in range(1, max_pages + 1):
            query = {**json_query, "page": {"type": "term", "value": page_number}}
            page_offers = await self.search(json_query=query)
            for offer in page_offers:
                offers.setdefault(offer.id, offer)
            if len(page_offers) < self.PAGE_SIZE:
                break
        return list(offers.values())

    @staticmethod
    def with_district(json_query: dict[str, Any], raion_id: int) -> dict[str, Any]:
        return {**json_query, "geo": {"type": "geo", "value": [{"type": "district", "id": raion_id}]}}

    async def search(self, json_query: dict[str, Any]) -> list[Offer]:
        """Произвольный запрос к API выдачи (страница своего фильтра, аренда вокруг точки).
        Не-200 от API (антибот отдаёт 403 с HTML) лечится повторным открытием выдачи во вкладке:
        быструю JS-капчу браузер проходит сам. Если и после этого не 200, это блокировка."""
        await self.ensure_open()
        if self._json_query is None:
            await self._establish_session()
        result = await self._post_search(json_query=json_query)
        if result.status != HTTPStatus.OK:
            logger.warning("search api answered %d, reopening the search page", result.status)
            await self._establish_session()
            result = await self._post_search(json_query=json_query)
        if result.status != HTTPStatus.OK:
            raise BlockedError(f"search api answered {result.status}")
        return parse_api_offers(body=result.text)

    async def _establish_session(self) -> None:
        """Открывает выдачу по URL из настроек и ждёт карточки, по одному контексту за раз:
        три новых посетителя с одного IP в одну секунду Циан встречает капчей, которая
        не проходит, а поодиночке сессия открывается за секунды. Блокировкой считаем случай,
        когда карточки так и не появились и адрес остался на странице капчи. Карточки появляются
        раньше, чем докачается конец HTML с JSON-блоком, поэтому перед чтением ждём DOMContentLoaded."""
        async with self._session_lock:
            page = self._require_page()
            timeout_ms = self._timeout_ms()
            started = time.monotonic()
            logger.info(
                "[%s] opening cian search page through proxy, up to %ds",
                self._role,
                self._settings.page_timeout_seconds,
            )
            await page.goto(self._settings.search_url, wait_until="commit", timeout=timeout_ms)
            logger.info(
                "[%s] cian search page responded in %.1fs, waiting for cards",
                self._role,
                time.monotonic() - started,
            )
            try:
                await page.wait_for_selector(self.CARD_SELECTOR, timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                logger.warning(
                    "[%s] no cards after %ds: url=%s title=%r",
                    self._role,
                    self._settings.page_timeout_seconds,
                    page.url[:120],
                    await page.title(),
                )
                if self.CAPTCHA_URL_MARKER in page.url:
                    raise BlockedError(f"captcha page not passed: {page.url}") from exc
                raise
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            self._json_query = parse_initial_state(html=await page.content()).results.json_query
            logger.info(
                "[%s] cian session established in %.1fs, jsonQuery captured",
                self._role,
                time.monotonic() - started,
            )

    async def _post_search(self, json_query: dict[str, Any]) -> PageFetchResult:
        page = self._require_page()
        body = json.dumps({"jsonQuery": json_query})
        raw = await asyncio.wait_for(
            page.evaluate(self.FETCH_SCRIPT, [self.API_URL, body]),
            timeout=self._settings.page_timeout_seconds,
        )
        return PageFetchResult.model_validate(raw)

    def _timeout_ms(self) -> float:
        return timedelta(seconds=self._settings.page_timeout_seconds) / timedelta(milliseconds=1)

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("cian context is not opened")
        return self._page
