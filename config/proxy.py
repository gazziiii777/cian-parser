from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CIAN_PROXY_", env_file=".env", extra="ignore")

    server: str = Field(description="Адрес прокси вида http://host:port или socks5://host:port")
    username: str = Field(description="Логин прокси")
    password: str = Field(description="Пароль прокси")
    rotate_url: str | None = Field(
        default=None,
        description="GET-ссылка смены IP у провайдера; без неё при блокировке только перезапуск браузера",
    )

    @property
    def host(self) -> str:
        return urlparse(self.server).hostname or self.server

    @property
    def port(self) -> int:
        return urlparse(self.server).port or 0

    @property
    def is_socks5(self) -> bool:
        return urlparse(self.server).scheme == "socks5"
