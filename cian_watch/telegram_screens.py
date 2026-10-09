from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any, ClassVar

from cian_watch.db.catalog import CatalogItem, CatalogPage, CatalogScope
from cian_watch.db.tables import OfferStatus
from cian_watch.formatting import millions, rubles, short_address


@dataclass
class Screen:
    text: str
    markup: dict[str, Any]


class Callbacks:
    """Формат callback_data кнопок (лимит Telegram 64 байта): menu, l:<scope>:<page>,
    c:<scope>:<page>:<offer>, cw/ch:<scope>:<page>:<offer> (слежение и скрытие из карточки),
    watch:<offer> и hide:<offer> под уведомлением."""

    MENU: ClassVar[str] = "menu"
    LIST: ClassVar[str] = "l"
    CARD: ClassVar[str] = "c"
    CARD_WATCH: ClassVar[str] = "cw"
    CARD_HIDE: ClassVar[str] = "ch"
    NOTIFY_WATCH: ClassVar[str] = "watch"
    NOTIFY_HIDE: ClassVar[str] = "hide"
    SEPARATOR: ClassVar[str] = ":"

    @classmethod
    def list_page(cls, scope: CatalogScope, page: int) -> str:
        return cls.SEPARATOR.join([cls.LIST, scope, str(page)])

    @classmethod
    def card(cls, action: str, scope: CatalogScope, page: int, offer_id: int) -> str:
        return cls.SEPARATOR.join([action, scope, str(page), str(offer_id)])

    @classmethod
    def notify(cls, action: str, offer_id: int) -> str:
        return cls.SEPARATOR.join([action, str(offer_id)])


class Screens:
    """Тексты и клавиатуры экранов бота. Чистое форматирование, без обращений к базе."""

    PAGE_SIZE: ClassVar[int] = 5
    SCOPE_TITLES: ClassVar[dict[CatalogScope, str]] = {
        CatalogScope.NEW: "🆕 По дате добавления",
        CatalogScope.PAYBACK: "💎 По окупаемости",
        CatalogScope.WATCHING: "⭐ Отслеживаю",
        CatalogScope.HIDDEN: "🚫 Скрытые",
    }
    EMPTY_TEXTS: ClassVar[dict[CatalogScope, str]] = {
        CatalogScope.NEW: "Активных объявлений нет.",
        CatalogScope.PAYBACK: "Нет объявлений с оценкой аренды.",
        CatalogScope.WATCHING: "Пока ничего не отслеживаешь.",
        CatalogScope.HIDDEN: "Скрытых нет.",
    }
    MENU_TEXT: ClassVar[str] = (
        "Новые квартиры приходят всем. «Отслеживать» под объявлением включает алерты по нему "
        "(цена, снятие), «Скрыть» убирает квартиру из твоих списков.\n\nЧто показать?"
    )
    NOT_FOUND_TEXT: ClassVar[str] = "Объявление не найдено."

    @classmethod
    def menu(cls) -> Screen:
        rows = [
            [{"text": cls.SCOPE_TITLES[scope], "callback_data": Callbacks.list_page(scope=scope, page=0)}]
            for scope in CatalogScope
        ]
        return Screen(text=cls.MENU_TEXT, markup={"inline_keyboard": rows})

    @classmethod
    def list_page(cls, page: CatalogPage, now: datetime) -> Screen:
        title = cls.SCOPE_TITLES[page.scope]
        if not page.items:
            return Screen(text=f"<b>{title}</b>\n\n{cls.EMPTY_TEXTS[page.scope]}", markup=cls._menu_only())
        lines = [f"<b>{title}</b> · {page.total}"]
        for number, item in enumerate(page.items, start=1):
            lines.append(f"\n<b>{number}.</b> {cls._list_line(item=item, now=now)}")
        flats = [
            [
                {
                    "text": f"{number}. {cls._button_label(item=item)}",
                    "callback_data": Callbacks.card(
                        action=Callbacks.CARD, scope=page.scope, page=page.page, offer_id=item.offer.id
                    ),
                }
            ]
            for number, item in enumerate(page.items, start=1)
        ]
        navigation = [
            {"text": "◀️", "callback_data": Callbacks.list_page(scope=page.scope, page=max(page.page - 1, 0))},
            {"text": f"{page.page + 1}/{page.pages}", "callback_data": Callbacks.MENU},
            {
                "text": "▶️",
                "callback_data": Callbacks.list_page(
                    scope=page.scope, page=min(page.page + 1, page.pages - 1)
                ),
            },
        ]
        rows = [*flats, navigation, [{"text": "🏠 Меню", "callback_data": Callbacks.MENU}]]
        return Screen(text="\n".join(lines), markup={"inline_keyboard": rows})

    @classmethod
    def card(cls, item: CatalogItem, scope: CatalogScope, page: int, now: datetime) -> Screen:
        offer = item.offer
        status = "" if offer.status == OfferStatus.ACTIVE else " · <b>снято</b>"
        lines = [
            f"<b>{rubles(offer.price)}</b> · {cls._info(offer.info)}{status}",
            short_address(offer.address),
        ]
        if offer.raion_name:
            lines.append(f"Район: {escape(offer.raion_name)}")
        if item.estimate is not None and item.estimate.market_price is not None:
            gap = (offer.price - item.estimate.market_price) / item.estimate.market_price * 100
            verdict = f"выше рынка на {gap:.0f}%" if gap >= 0 else f"ниже рынка на {-gap:.0f}%"
            lines.append(
                f"Домклик: {rubles(item.estimate.market_price)}, {verdict} "
                f"({cls._signed_millions(offer.price - item.estimate.market_price)})"
            )
        if item.first_price != offer.price:
            lines.append(cls._price_history_line(item=item))
        payback = item.payback_years()
        if item.rent is not None and payback is not None:
            lines.append(
                f"Аренда рядом: {rubles(item.rent.median_rent)}/мес ({escape(item.rent.scope)}), "
                f"окупаемость {cls._years(payback)}"
            )
        lines.append(f"Добавлено {cls._age(offer.cian_created_at, now)}")
        links = [f'<a href="{offer.url}">Циан</a>']
        if item.estimate is not None and item.estimate.url:
            links.append(f'<a href="{item.estimate.url}">Домклик</a>')
        lines.append(" | ".join(links))
        actions = [
            {
                "text": "✅ Отслеживаю, снять" if item.watching else "⭐ Отслеживать",
                "callback_data": Callbacks.card(
                    action=Callbacks.CARD_WATCH, scope=scope, page=page, offer_id=offer.id
                ),
            },
            {
                "text": "↩️ Показывать" if item.hidden else "🚫 Скрыть",
                "callback_data": Callbacks.card(
                    action=Callbacks.CARD_HIDE, scope=scope, page=page, offer_id=offer.id
                ),
            },
        ]
        back = [
            {"text": "◀️ К списку", "callback_data": Callbacks.list_page(scope=scope, page=page)},
            {"text": "🏠 Меню", "callback_data": Callbacks.MENU},
        ]
        return Screen(text="\n".join(lines), markup={"inline_keyboard": [actions, back]})

    @classmethod
    def not_found(cls) -> Screen:
        return Screen(text=cls.NOT_FOUND_TEXT, markup=cls._menu_only())

    @staticmethod
    def notification_markup(offer_id: int, watching: bool, hidden: bool) -> dict[str, Any]:
        if hidden:
            button = {
                "text": "↩️ Показывать снова",
                "callback_data": Callbacks.notify(action=Callbacks.NOTIFY_HIDE, offer_id=offer_id),
            }
            return {"inline_keyboard": [[button]]}
        watch = {
            "text": "✅ Отслеживаю, снять" if watching else "⭐ Отслеживать",
            "callback_data": Callbacks.notify(action=Callbacks.NOTIFY_WATCH, offer_id=offer_id),
        }
        hide = {
            "text": "🚫 Скрыть",
            "callback_data": Callbacks.notify(action=Callbacks.NOTIFY_HIDE, offer_id=offer_id),
        }
        return {"inline_keyboard": [[watch, hide]]}

    @classmethod
    def _list_line(cls, item: CatalogItem, now: datetime) -> str:
        """Три строки на квартиру: цена со ссылкой на Циан и параметры, адрес, оценка и аренда."""
        offer = item.offer
        marks = ("⭐" if item.watching else "") + ("🚫" if item.hidden else "")
        head = [f'<a href="{offer.url}"><b>{millions(offer.price)} млн</b></a>', cls._info(offer.info)]
        if offer.status != OfferStatus.ACTIVE:
            head.append("<b>снято</b>")
        if offer.raion_name:
            head.append(escape(offer.raion_name))
        facts = []
        if item.estimate is not None and item.estimate.market_price is not None:
            gap = (offer.price - item.estimate.market_price) / item.estimate.market_price * 100
            name = f'<a href="{item.estimate.url}">Домклик</a>' if item.estimate.url else "Домклик"
            facts.append(
                f"{name} {millions(item.estimate.market_price)} млн "
                f"({cls._signed_millions(offer.price - item.estimate.market_price)}, {gap:+.0f}%)"
            )
        payback = item.payback_years()
        if item.rent is not None and payback is not None:
            facts.append(
                f"аренда {cls._thousands(item.rent.median_rent)} тыс/мес, окупаемость {cls._years(payback)}"
            )
        facts.append(cls._age(offer.cian_created_at, now))
        lines = [" · ".join(head) + (f" {marks}" if marks else ""), short_address(offer.address)]
        if item.first_price != offer.price:
            lines.append(cls._price_history_line(item=item))
        lines.append(" · ".join(facts))
        return "\n".join(lines)

    @classmethod
    def _price_history_line(cls, item: CatalogItem) -> str:
        arrow = "📉" if item.offer.price < item.first_price else "📈"
        return (
            f"{arrow} Было {millions(item.first_price)} млн на Циане: "
            f"{cls._signed_millions(item.offer.price - item.first_price)} "
            f"({item.price_change_percent():+.0f}%)"
        )

    @classmethod
    def _signed_millions(cls, delta: int) -> str:
        sign = "+" if delta >= 0 else "−"
        return f"{sign}{millions(abs(delta))} млн"

    @classmethod
    def _button_label(cls, item: CatalogItem) -> str:
        """Подпись кнопки-квартиры: цена, комнаты, район. Коротко, на узком экране подпись режется."""
        offer = item.offer
        rooms = " ".join(offer.info.split()).split("·", maxsplit=1)[0].strip()
        parts = [f"{millions(offer.price)} млн", rooms]
        if offer.raion_name:
            parts.append(offer.raion_name)
        return " · ".join(parts)

    @staticmethod
    def _thousands(price: int) -> str:
        return f"{price / 1_000:.0f}"

    @staticmethod
    def _menu_only() -> dict[str, Any]:
        return {"inline_keyboard": [[{"text": "🏠 Меню", "callback_data": Callbacks.MENU}]]}

    @staticmethod
    def _age(created_at: datetime, now: datetime) -> str:
        days = (now - created_at.astimezone(UTC)).days
        if days <= 0:
            return "сегодня"
        if days == 1:
            return "вчера"
        return f"{days} дн. назад"

    @staticmethod
    def _info(info: str) -> str:
        return escape(" ".join(info.split()))

    @staticmethod
    def _years(value: float) -> str:
        years = round(value)
        if years % 10 == 1 and years % 100 != 11:
            return f"{years} год"
        if years % 10 in {2, 3, 4} and years % 100 not in {12, 13, 14}:
            return f"{years} года"
        return f"{years} лет"
