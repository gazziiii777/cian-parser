import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from pydantic import ValidationError

from cian_watch.db.catalog import CatalogRepository, CatalogScope
from cian_watch.db.users import UsersRepository
from cian_watch.db.watchlist import WatchlistRepository
from cian_watch.models import TelegramUpdate, TelegramUpdatesResponse
from cian_watch.models.telegram import TelegramCallbackQuery, TelegramMessage
from cian_watch.telegram_api import TelegramApi
from cian_watch.telegram_screens import Callbacks, Screen, Screens

logger = logging.getLogger(__name__)


class TelegramBot:
    """Входящая сторона бота через длинный опрос getUpdates: меню со списками квартир
    (одно сообщение перерисовывается при навигации), карточка, слежение и скрытие,
    кнопки под уведомлениями. Чаты не из users (или отозванные) получают отказ."""

    POLL_TIMEOUT_SECONDS: ClassVar[int] = 30
    POLL_GRACE_SECONDS: ClassVar[float] = 10.0
    ERROR_PAUSE_SECONDS: ClassVar[float] = 5.0
    NO_ACCESS_TEXT: ClassVar[str] = "Нет доступа"
    COMMANDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("menu", "Списки квартир: новые, окупаемость, слежение"),
    )
    NOT_MODIFIED_MARKER: ClassVar[str] = "message is not modified"

    def __init__(
        self,
        api: TelegramApi,
        users: UsersRepository,
        watchlist: WatchlistRepository,
        catalog: CatalogRepository,
    ) -> None:
        self._api = api
        self._users = users
        self._watchlist = watchlist
        self._catalog = catalog

    async def run(self) -> None:
        """Регистрирует команды в подсказках Telegram и крутит бесконечный опрос. Сетевые сбои
        и кривой ответ только логируются, опрос продолжается с паузой; смещение сдвигается только
        за обработанными апдейтами, чтобы не потерять нажатие."""
        await self._register_commands()
        offset: int | None = None
        try:
            while True:
                try:
                    updates = await self._fetch_updates(offset=offset)
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.warning("telegram getUpdates failed: %s", exc)
                    await asyncio.sleep(self.ERROR_PAUSE_SECONDS)
                    continue
                for update in updates:
                    await self._handle(update=update)
                    offset = update.update_id + 1
        finally:
            await self._confirm_handled(offset=offset)

    async def _confirm_handled(self, offset: int | None) -> None:
        """Подтверждает обработанные апдейты при остановке: иначе после перезапуска (деплой)
        Telegram отдаст последнюю пачку ещё раз, и нажатия переключатся обратно."""
        if offset is None:
            return
        try:
            await self._api.call(method="getUpdates", payload={"offset": offset, "timeout": 0, "limit": 1})
        except httpx.HTTPError as exc:
            logger.warning("telegram updates not confirmed on shutdown: %s", exc)

    async def _register_commands(self) -> None:
        commands = [{"command": name, "description": description} for name, description in self.COMMANDS]
        try:
            await self._api.call(method="setMyCommands", payload={"commands": commands})
        except httpx.HTTPError as exc:
            logger.warning("telegram setMyCommands failed: %s", exc)

    async def _fetch_updates(self, offset: int | None) -> list[TelegramUpdate]:
        payload = {
            "timeout": self.POLL_TIMEOUT_SECONDS,
            "offset": offset,
            "allowed_updates": ["message", "callback_query"],
        }
        raw = await self._api.call(
            method="getUpdates", payload=payload, timeout=self.POLL_TIMEOUT_SECONDS + self.POLL_GRACE_SECONDS
        )
        return TelegramUpdatesResponse.model_validate(raw).result

    async def _handle(self, update: TelegramUpdate) -> None:
        try:
            if update.callback_query is not None:
                await self._handle_callback(query=update.callback_query)
            elif update.message is not None and update.message.text is not None:
                await self._handle_message(message=update.message)
        except httpx.HTTPStatusError as exc:
            logger.warning("telegram update %d not handled: %s", update.update_id, exc.response.text)
        except httpx.HTTPError as exc:
            logger.warning("telegram update %d not handled: %s", update.update_id, exc)

    async def _handle_message(self, message: TelegramMessage) -> None:
        """Любой текст, включая /start и /menu, открывает меню новым сообщением."""
        chat_id = message.chat.id
        if chat_id not in await self._users.active_chat_ids():
            await self._send(chat_id=chat_id, screen=Screen(text=self.NO_ACCESS_TEXT, markup={}))
            return
        await self._send(chat_id=chat_id, screen=Screens.menu())

    async def _handle_callback(self, query: TelegramCallbackQuery) -> None:
        """Разбирает callback_data и перерисовывает то же сообщение. Ответ на callback
        обязателен, иначе у пользователя висят часики на кнопке."""
        if query.message is None or not query.data:
            await self._answer(query_id=query.id, text=None)
            return
        chat_id = query.message.chat.id
        if chat_id not in await self._users.active_chat_ids():
            await self._answer(query_id=query.id, text=self.NO_ACCESS_TEXT)
            return
        try:
            await self._route_callback(query=query, chat_id=chat_id, data=query.data)
        except ValueError:
            logger.warning("telegram callback ignored, bad data %r from chat %d", query.data, chat_id)
            await self._answer(query_id=query.id, text=None)

    async def _route_callback(self, query: TelegramCallbackQuery, chat_id: int, data: str) -> None:
        """callback_data приходит от клиента и не доверяется: неизвестное действие или не число
        в полях поднимает ValueError, который гасится выше."""
        if query.message is None:
            return
        parts = data.split(Callbacks.SEPARATOR)
        action = parts[0]
        toast: str | None = None
        if action == Callbacks.MENU:
            screen = Screens.menu()
        elif action == Callbacks.LIST and len(parts) == 3:
            screen = await self._list(chat_id=chat_id, scope=CatalogScope(parts[1]), page=int(parts[2]))
        elif action in {Callbacks.CARD, Callbacks.CARD_WATCH, Callbacks.CARD_HIDE} and len(parts) == 4:
            scope, page, offer_id = CatalogScope(parts[1]), int(parts[2]), int(parts[3])
            toast = await self._apply_card_action(action=action, chat_id=chat_id, offer_id=offer_id)
            screen = await self._card(chat_id=chat_id, scope=scope, page=page, offer_id=offer_id)
        elif action in {Callbacks.NOTIFY_WATCH, Callbacks.NOTIFY_HIDE} and len(parts) == 2:
            offer_id = int(parts[1])
            toast, markup = await self._apply_notification_action(
                action=action, chat_id=chat_id, offer_id=offer_id
            )
            await self._answer(query_id=query.id, text=toast)
            await self._api.call(
                method="editMessageReplyMarkup",
                payload={"chat_id": chat_id, "message_id": query.message.message_id, "reply_markup": markup},
            )
            return
        else:
            raise ValueError(f"unknown callback action {action!r}")
        await self._answer(query_id=query.id, text=toast)
        await self._edit(chat_id=chat_id, message_id=query.message.message_id, screen=screen)

    async def _list(self, chat_id: int, scope: CatalogScope, page: int) -> Screen:
        catalog_page = await self._catalog.browse(
            telegram_chat_id=chat_id, scope=scope, page=page, page_size=Screens.PAGE_SIZE
        )
        return Screens.list_page(page=catalog_page, now=datetime.now(UTC))

    async def _card(self, chat_id: int, scope: CatalogScope, page: int, offer_id: int) -> Screen:
        item = await self._catalog.item(telegram_chat_id=chat_id, offer_id=offer_id)
        if item is None:
            return Screens.not_found()
        return Screens.card(item=item, scope=scope, page=page, now=datetime.now(UTC))

    async def _apply_card_action(self, action: str, chat_id: int, offer_id: int) -> str | None:
        if action == Callbacks.CARD_WATCH:
            return (await self._toggle_watch(chat_id=chat_id, offer_id=offer_id))[0]
        if action == Callbacks.CARD_HIDE:
            return (await self._toggle_hidden(chat_id=chat_id, offer_id=offer_id))[0]
        return None

    async def _apply_notification_action(
        self, action: str, chat_id: int, offer_id: int
    ) -> tuple[str, dict[str, Any]]:
        """Кнопки под уведомлением после нажатия строятся из результата переключения: скрытие
        снимает слежение, а кнопка слежения доступна только у нескрытого, запрос в базу не нужен."""
        if action == Callbacks.NOTIFY_WATCH:
            toast, watching = await self._toggle_watch(chat_id=chat_id, offer_id=offer_id)
            return toast, Screens.notification_markup(offer_id=offer_id, watching=watching, hidden=False)
        toast, hidden = await self._toggle_hidden(chat_id=chat_id, offer_id=offer_id)
        return toast, Screens.notification_markup(offer_id=offer_id, watching=False, hidden=hidden)

    async def _toggle_watch(self, chat_id: int, offer_id: int) -> tuple[str, bool]:
        watching = await self._watchlist.toggle(
            telegram_chat_id=chat_id, offer_id=offer_id, now=datetime.now(UTC)
        )
        logger.info("watchlist %s: chat %d, offer %d", "added" if watching else "removed", chat_id, offer_id)
        return ("Отслеживаю" if watching else "Больше не отслеживаю"), watching

    async def _toggle_hidden(self, chat_id: int, offer_id: int) -> tuple[str, bool]:
        hidden = await self._catalog.toggle_hidden(
            telegram_chat_id=chat_id, offer_id=offer_id, now=datetime.now(UTC)
        )
        logger.info("hidden %s: chat %d, offer %d", "added" if hidden else "removed", chat_id, offer_id)
        return ("Скрыто, больше не покажу" if hidden else "Снова в списках"), hidden

    async def _answer(self, query_id: str, text: str | None) -> None:
        payload: dict[str, str] = {"callback_query_id": query_id}
        if text is not None:
            payload["text"] = text
        await self._api.call(method="answerCallbackQuery", payload=payload)

    async def _send(self, chat_id: int, screen: Screen) -> None:
        await self._api.call(
            method="sendMessage", payload=self._message_payload(chat_id=chat_id, screen=screen)
        )

    async def _edit(self, chat_id: int, message_id: int, screen: Screen) -> None:
        """Перерисовка того же сообщения. Telegram отвечает 400, если текст и кнопки не изменились
        (повторное нажатие той же страницы): это не ошибка, остальные 4xx и 5xx поднимаются."""
        payload = self._message_payload(chat_id=chat_id, screen=screen)
        payload["message_id"] = message_id
        try:
            await self._api.call(method="editMessageText", payload=payload)
        except httpx.HTTPStatusError as exc:
            if self.NOT_MODIFIED_MARKER not in exc.response.text:
                raise

    @staticmethod
    def _message_payload(chat_id: int, screen: Screen) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": screen.text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if screen.markup:
            payload["reply_markup"] = screen.markup
        return payload
