from types import TracebackType
from typing import Any, ClassVar, Self

import httpx


class TelegramApi:
    """Один httpx-клиент на бота: уведомления и опрос апдейтов ходят через него,
    через прокси из настроек (из РФ Bot API напрямую закрыт)."""

    BASE_URL: ClassVar[str] = "https://api.telegram.org/bot{token}/{method}"
    REQUEST_TIMEOUT_SECONDS: ClassVar[float] = 15.0

    def __init__(self, bot_token: str, proxy: str | None) -> None:
        self._token = bot_token
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

    async def call(
        self, method: str, payload: dict[str, Any], timeout: float | None = None
    ) -> Any:  # raw Bot API json before validation
        """Ответ Bot API как есть; статус не 2xx поднимает httpx.HTTPStatusError, тело не JSON
        (страница ошибки прокси) httpx.DecodingError: вызывающие ловят общий httpx.HTTPError."""
        response = await self._client.post(
            self.BASE_URL.format(token=self._token, method=method),
            json=payload,
            timeout=timeout if timeout is not None else self.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise httpx.DecodingError(
                f"telegram {method}: body is not json", request=response.request
            ) from exc
