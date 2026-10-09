import asyncio
import logging
import time
from typing import ClassVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class RotationResponse(BaseModel):
    success: bool


class ProxyRotator:
    """Смена выходного IP мобильного прокси по ссылке провайдера. Провайдер принимает вызов
    не чаще раза в минуту, сам запрос держит соединение около 15 секунд, пока модем
    перерегистрируется, после ответа даём каналу ещё немного устояться."""

    MIN_INTERVAL_SECONDS: ClassVar[float] = 60.0
    REQUEST_TIMEOUT_SECONDS: ClassVar[float] = 90.0
    SETTLE_SECONDS: ClassVar[float] = 15.0

    def __init__(self, rotate_url: str) -> None:
        self._rotate_url = rotate_url
        self._rotated_at = 0.0

    async def rotate(self) -> bool:
        """Возвращает True, если IP сменён. Слишком частый вызов пропускается, чтобы не
        упереться в лимит провайдера; сбой сети или отказ провайдера только логируется."""
        since_last = time.monotonic() - self._rotated_at
        if since_last < self.MIN_INTERVAL_SECONDS:
            logger.info("proxy ip rotated %.0fs ago, skipping rotation", since_last)
            return False
        self._rotated_at = time.monotonic()
        logger.info("rotating proxy ip")
        try:
            async with httpx.AsyncClient(
                timeout=self.REQUEST_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                response = await client.get(self._rotate_url)
                response.raise_for_status()
                result = RotationResponse.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as exc:
            logger.warning("proxy ip rotation failed: %s", exc)
            return False
        if not result.success:
            logger.warning("proxy provider refused ip rotation")
            return False
        logger.info("proxy ip rotated, settling for %.0fs", self.SETTLE_SECONDS)
        await asyncio.sleep(self.SETTLE_SECONDS)
        return True
