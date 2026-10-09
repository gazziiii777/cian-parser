import asyncio
import logging
from collections.abc import Awaitable, Callable
from html import escape
from types import TracebackType
from typing import ClassVar, Self

import httpx

from cian_watch.models import DomclickEstimate, Offer, PriceChange, RentedStats, RentEstimate

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Шлёт уведомления в один чат через Bot API. Любая ошибка отправки только логируется:
    уведомление вторично, цикл опроса и запись в базу важнее."""

    API_URL: ClassVar[str] = "https://api.telegram.org/bot{token}/sendMessage"
    REQUEST_TIMEOUT_SECONDS: ClassVar[float] = 15.0
    PAUSE_BETWEEN_MESSAGES_SECONDS: ClassVar[float] = 0.3
    RUBLES_IN_MILLION: ClassVar[int] = 1_000_000

    def __init__(
        self, bot_token: str, recipients: Callable[[], Awaitable[list[int]]], proxy: str | None
    ) -> None:
        self._url = self.API_URL.format(token=bot_token)
        self._recipients = recipients
        self._client = httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT_SECONDS, proxy=proxy)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def notify_new_offer(
        self,
        offer: Offer,
        estimate: DomclickEstimate | None,
        house_not_found: bool,
        rent: RentEstimate | None,
        rented: RentedStats | None,
    ) -> None:
        lines = [
            f"🆕 <b>{escape(offer.formatted_full_price)}</b> · {escape(offer.formatted_full_info)}",
            escape(offer.address),
        ]
        if estimate is not None:
            lines.append(self._estimate_line(offer=offer, estimate=estimate))
        elif house_not_found:
            lines.append("Домклик: дом по этому адресу не найден")
        else:
            lines.append("Домклик: не ответил, оценку пришлю отдельно")
        if rent is not None:
            lines.append(self._rent_line(offer=offer, rent=rent))
        if rented is not None:
            lines.append(
                f"Сдано в районе {escape(rented.raion_name)} за {rented.days} дн.: {rented.count}, "
                f"медиана {self._rubles(rented.median_rent)}/мес, "
                f"в среднем за {rented.median_exposure_days} дн."
            )
        lines.append(self._links(offer=offer, estimate=estimate))
        await self._send("\n".join(lines))

    async def notify_late_estimate(self, offer: Offer, estimate: DomclickEstimate) -> None:
        lines = [
            f"📊 Оценка: <b>{escape(offer.formatted_full_price)}</b> · {escape(offer.formatted_full_info)}",
            escape(offer.address),
            self._estimate_line(offer=offer, estimate=estimate),
            self._links(offer=offer, estimate=estimate),
        ]
        await self._send("\n".join(lines))

    async def notify_price_change(self, change: PriceChange) -> None:
        offer = change.offer
        percent = (offer.price - change.old_price) / change.old_price * 100
        lines = [
            f"💰 {self._rubles(change.old_price)} → <b>{self._rubles(offer.price)}</b> ({percent:+.0f}%)"
            f" · {escape(offer.formatted_full_info)}",
            escape(offer.address),
            f'<a href="{offer.url}">Циан</a>',
        ]
        await self._send("\n".join(lines))

    def _rent_line(self, offer: Offer, rent: RentEstimate) -> str:
        payback_years = offer.price / (rent.median_rent * 12)
        return (
            f"Аренда рядом: {self._rubles(rent.median_rent)}/мес, медиана по {rent.sample_size} похожим "
            f"({escape(rent.scope)}), окупаемость {payback_years:.0f} лет"
        )

    @staticmethod
    def _links(offer: Offer, estimate: DomclickEstimate | None) -> str:
        links = [f'<a href="{offer.url}">Циан</a>']
        if estimate is not None:
            links.append(f'<a href="{estimate.url}">Домклик</a>')
        return " | ".join(links)

    def _estimate_line(self, offer: Offer, estimate: DomclickEstimate) -> str:
        gap = (offer.price - estimate.market_price) / estimate.market_price * 100
        verdict = f"выше рынка на {gap:.0f}%" if gap >= 0 else f"ниже рынка на {-gap:.0f}%"
        return (
            f"Домклик: {self._rubles(estimate.market_price)} "
            f"({self._millions(estimate.min_price)}–{self._millions(estimate.max_price)} млн), {verdict}"
        )

    async def _send(self, text: str) -> None:
        """Одно и то же сообщение всем активным пользователям; список берётся из базы при каждой
        отправке, чтобы выданный доступ действовал без перезапуска."""
        chat_ids = await self._recipients()
        for chat_id in chat_ids:
            await self._send_to(chat_id=chat_id, text=text)
        logger.info("telegram message sent to %d users: %s", len(chat_ids), text.splitlines()[0])

    async def _send_to(self, chat_id: int, text: str) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = await self._client.post(self._url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("telegram message not sent to %d: %s", chat_id, exc)
            return
        await asyncio.sleep(self.PAUSE_BETWEEN_MESSAGES_SECONDS)

    @staticmethod
    def _rubles(price: int) -> str:
        return f"{price:,}".replace(",", " ") + " ₽"

    def _millions(self, price: int) -> str:
        return f"{price / self.RUBLES_IN_MILLION:.1f}".replace(".", ",")
