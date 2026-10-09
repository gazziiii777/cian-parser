import logging
from types import TracebackType
from typing import ClassVar, Self, cast

from camoufox.async_api import AsyncCamoufox, AsyncNewContext
from playwright.async_api import Browser, BrowserContext

from cian_watch.proxy_forwarder import ProxyForwarder
from config.settings import Settings

logger = logging.getLogger(__name__)


class BrowserHost:
    """Один Camoufox на процесс и форвардер прокси при нём. Потребители (выдача Циана,
    оценка Домклика) получают отдельные контексты, каждый со своим отпечатком и куками."""

    LOCALE: ClassVar[str] = "ru-RU"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._forwarder: ProxyForwarder | None = None
        self._camoufox: AsyncCamoufox | None = None
        self._browser: Browser | None = None
        self.generation = 0

    async def __aenter__(self) -> Self:
        await self.launch()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.shutdown()

    async def launch(self) -> None:
        """Поднимает форвардер (если прокси включён) и браузер, именно в этом порядке:
        контекстам нужен адрес форвардера."""
        proxy = self._settings.proxy()
        if proxy is not None:
            self._forwarder = await ProxyForwarder(upstream=proxy).__aenter__()
        self._camoufox = AsyncCamoufox(  # type: ignore[no-untyped-call]
            headless=self._settings.headless.as_camoufox(), block_webrtc=True, locale=self.LOCALE
        )
        self._browser = cast(Browser, await self._camoufox.__aenter__())
        logger.info("browser launched, proxy=%s", proxy.server if proxy else "none")

    async def shutdown(self) -> None:
        if self._camoufox is not None:
            await self._camoufox.__aexit__(None, None, None)
        if self._forwarder is not None:
            await self._forwarder.__aexit__(None, None, None)
        self._camoufox = None
        self._browser = None
        self._forwarder = None

    async def restart(self) -> None:
        """Каждый перезапуск повышает поколение: контексты, открытые в старом поколении,
        мертвы, и их владельцы переоткрывают их при следующем обращении."""
        await self.shutdown()
        await self.launch()
        self.generation += 1

    async def new_context(self, use_proxy: bool) -> BrowserContext:
        """Прокси подключается на уровне контекста и через локальный форвардер без пароля:
        с паролем напрямую Firefox через раз не отвечает на 407 и навигация виснет.
        webrtc_ip передаём, чтобы Camoufox не ходил за IP выхода во внешние сервисы
        (прокси их режет); с block_webrtc значение всё равно не утекает."""
        if self._browser is None:
            raise RuntimeError("browser is not launched")
        proxy = self._settings.proxy() if use_proxy else None
        return await AsyncNewContext(
            self._browser,
            proxy={"server": self._forwarder.server_url} if proxy and self._forwarder else None,
            webrtc_ip=proxy.host if proxy else None,
            timezone_id=self._settings.timezone_id,
            locale=self.LOCALE,
        )
