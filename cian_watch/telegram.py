import asyncio
import logging
from collections.abc import Awaitable, Callable
from html import escape
from http import HTTPStatus
from typing import Any, ClassVar

import httpx

from cian_watch.db.tables import OfferRow
from cian_watch.formatting import millions, rubles, short_address
from cian_watch.models import DomclickEstimate, Offer, PriceChange, RentedStats, RentEstimate
from cian_watch.telegram_api import TelegramApi
from cian_watch.telegram_screens import Screens

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Шлёт уведомления через Bot API. Новые объявления уходят всем активным пользователям,
    алерты по объявлению только его наблюдателям. Любая ошибка отправки только логируется:
    уведомление вторично, цикл опроса и запись в базу важнее."""

    PAUSE_BETWEEN_MESSAGES_SECONDS: ClassVar[float] = 0.3
    RETRY_AFTER_FALLBACK_SECONDS: ClassVar[float] = 5.0

    def __init__(self, api: TelegramApi, recipients: Callable[[], Awaitable[list[int]]]) -> None:
        self._api = api
        self._recipients = recipients

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
                f"медиана {rubles(rented.median_rent)}/мес, "
                f"в среднем за {rented.median_exposure_days} дн."
            )
        lines.append(self._links(offer=offer, estimate=estimate))
        await self._send_all(
            text="\n".join(lines),
            reply_markup=Screens.notification_markup(offer_id=offer.id, watching=False, hidden=False),
        )

    async def notify_late_estimate(self, offer: Offer, estimate: DomclickEstimate) -> None:
        lines = [
            f"📊 Оценка: <b>{escape(offer.formatted_full_price)}</b> · {escape(offer.formatted_full_info)}",
            escape(offer.address),
            self._estimate_line(offer=offer, estimate=estimate),
            self._links(offer=offer, estimate=estimate),
        ]
        await self._send_all(text="\n".join(lines))

    async def notify_price_change(self, change: PriceChange, chat_ids: list[int]) -> None:
        offer = change.offer
        percent = (offer.price - change.old_price) / change.old_price * 100
        icon = "📉" if offer.price < change.old_price else "📈"
        lines = [
            f"{icon} {rubles(change.old_price)} → <b>{rubles(offer.price)}</b> ({percent:+.0f}%)"
            f" · {escape(offer.formatted_full_info)}",
            escape(offer.address),
            f'<a href="{offer.url}">Циан</a>',
        ]
        await self._send_to_many(text="\n".join(lines), chat_ids=chat_ids)

    async def notify_gone(self, row: OfferRow, chat_ids: list[int]) -> None:
        lines = [
            f"🚫 Снято с продажи: <b>{rubles(row.price)}</b> · {escape(row.info)}",
            short_address(row.address),
            f'<a href="{row.url}">Циан</a>',
        ]
        await self._send_to_many(text="\n".join(lines), chat_ids=chat_ids)

    async def notify_reappeared(self, offer: Offer, chat_ids: list[int]) -> None:
        lines = [
            f"↩️ Снова в продаже: <b>{escape(offer.formatted_full_price)}</b>"
            f" · {escape(offer.formatted_full_info)}",
            escape(offer.address),
            f'<a href="{offer.url}">Циан</a>',
        ]
        await self._send_to_many(text="\n".join(lines), chat_ids=chat_ids)

    def _rent_line(self, offer: Offer, rent: RentEstimate) -> str:
        payback_years = offer.price / (rent.median_rent * 12)
        return (
            f"Аренда рядом: {rubles(rent.median_rent)}/мес, медиана по {rent.sample_size} похожим "
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
            f"Домклик: {rubles(estimate.market_price)} "
            f"({millions(estimate.min_price)}–{millions(estimate.max_price)} млн), {verdict}"
        )

    async def _send_all(self, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        """Список получателей берётся из базы при каждой отправке, чтобы выданный доступ
        действовал без перезапуска."""
        await self._send_to_many(text=text, chat_ids=await self._recipients(), reply_markup=reply_markup)

    async def _send_to_many(
        self, text: str, chat_ids: list[int], reply_markup: dict[str, Any] | None = None
    ) -> None:
        for chat_id in chat_ids:
            await self._send_to(chat_id=chat_id, text=text, reply_markup=reply_markup)
        logger.info("telegram message sent to %d users: %s", len(chat_ids), text.splitlines()[0])

    async def _send_to(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            await self._api.call(method="sendMessage", payload=payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
                logger.warning("telegram message not sent to %d: %s", chat_id, exc.response.text)
                return
            await self._wait_rate_limit(response=exc.response, chat_id=chat_id)
            await self._send_to(chat_id=chat_id, text=text, reply_markup=reply_markup)
            return
        except httpx.HTTPError as exc:
            logger.warning("telegram message not sent to %d: %s", chat_id, exc)
            return
        await asyncio.sleep(self.PAUSE_BETWEEN_MESSAGES_SECONDS)

    async def _wait_rate_limit(self, response: httpx.Response, chat_id: int) -> None:
        """Telegram на 429 называет паузу в parameters.retry_after; ждём её и шлём заново один раз."""
        try:
            seconds = float(response.json()["parameters"]["retry_after"])
        except (ValueError, KeyError, TypeError):
            seconds = self.RETRY_AFTER_FALLBACK_SECONDS
        logger.warning("telegram rate limit for %d, waiting %.0fs", chat_id, seconds)
        await asyncio.sleep(seconds)
