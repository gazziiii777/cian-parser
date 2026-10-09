import logging
from enum import StrEnum
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.proxy import ProxySettings

DEFAULT_SEARCH_URL = (
    "https://www.cian.ru/cat.php?currency=2&deal_type=sale&engine_version=2&flat_share=2"
    "&maxprice=24000000&offer_type=flat&only_flat=1&region=1"
)
NEWEST_FIRST_SORT = "creation_date_desc"


class HeadlessMode(StrEnum):
    ON = "true"
    OFF = "false"
    VIRTUAL = "virtual"

    def as_camoufox(self) -> bool | Literal["virtual"]:
        if self is HeadlessMode.VIRTUAL:
            return "virtual"
        return self is HeadlessMode.ON


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OFF = "OFF"

    def threshold(self) -> int:
        if self is LogLevel.OFF:
            return logging.CRITICAL + 1
        return logging.getLevelNamesMapping()[self.value]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CIAN_", env_file=".env", extra="ignore")

    search_url: str = Field(
        default=DEFAULT_SEARCH_URL,
        description="URL выдачи Циана с сортировкой по дате добавления, берётся из адресной строки сайта",
    )
    pages: int = Field(
        default=100,
        ge=1,
        le=200,
        description="Потолок страниц ленты за опрос; обход останавливается на первой странице без новых",
    )
    sweep_interval_minutes: int = Field(
        default=120,
        ge=5,
        description="Как часто каждый район с активными объявлениями обходится целиком (цены, снятые)",
    )
    sweep_pause_seconds: float = Field(
        default=2.0,
        ge=0,
        description="Пауза между районами в обходе, чтобы не долбить API без передышки",
    )
    sweep_pages_max: int = Field(
        default=25,
        ge=1,
        description="Потолок страниц на район при обходе",
    )
    sweep_misses_to_sell: int = Field(
        default=2,
        ge=1,
        description="Сколько обходов подряд без объявления, чтобы считать его снятым с продажи",
    )
    poll_interval_seconds: int = Field(
        default=180,
        ge=30,
        description="Пауза между опросами выдачи, к ней добавляется случайный джиттер",
    )
    poll_jitter_seconds: int = Field(
        default=30,
        ge=0,
        description="Верхняя граница случайной добавки к паузе, чтобы опросы не шли ровно по таймеру",
    )
    retry_attempts: int = Field(
        default=5,
        ge=1,
        description="Сколько раз подряд перезапускать браузер с новым отпечатком, если выдача не открылась",
    )
    retry_delay_seconds: int = Field(
        default=10,
        ge=1,
        description="Пауза перед повтором, умножается на номер попытки: 10, 20, 30 секунд и так далее",
    )
    page_timeout_seconds: int = Field(
        default=60,
        ge=5,
        description="Сколько ждать карточки на странице, после этого считаем попытку провальной",
    )
    headless: HeadlessMode = Field(
        default=HeadlessMode.ON,
        description="true: без окна, false: с окном (нужен экран), virtual: окно на Xvfb, для докера",
    )
    timezone_id: str = Field(
        default="Europe/Moscow",
        description="Таймзона браузера, должна совпадать с регионом прокси",
    )
    use_proxy: bool = Field(
        default=False,
        description="Ходить через прокси из переменных CIAN_PROXY_*, без него идём с домашнего IP",
    )
    telegram_bot_token: str | None = Field(
        default=None,
        description="Токен бота из @BotFather; без него и chat_id уведомления выключены, остаётся консоль",
    )
    telegram_chat_id: int | None = Field(
        default=None,
        description="chat_id владельца, заводится в users при старте; остальным доступ даёт cian-watch-users",
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Порог для консоли (stdout), каждая строка это JSON-объект",
    )
    gelf_host: str | None = Field(
        default=None,
        description="Хост Graylog с GELF UDP инпутом; пусто: логи только в консоль",
    )
    gelf_port: int = Field(default=12201, ge=1, le=65535, description="Порт GELF-инпута Graylog")
    gelf_log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Порог для Graylog, не зависит от консольного; OFF выключает поток",
    )
    graylog_stream: str = Field(
        default="cian-parser",
        description="Поле stream в каждой записи, по нему правило стрима в Graylog",
    )
    telegram_proxy: str | None = Field(
        default=None,
        description="HTTP-прокси для Bot API (из РФ api.telegram.org закрыт): дома это gost на сервере",
    )
    db_host: str = Field(default="localhost", description="Хост Postgres, в compose это сервис db")
    db_port: int = Field(default=5433, description="Порт Postgres, локально 5433, внутри compose 5432")
    db_user: str = Field(default="cian", description="Пользователь Postgres")
    db_password: str = Field(description="Пароль Postgres, тот же, что получает контейнер db")
    db_name: str = Field(default="cian", description="Имя базы")

    @field_validator("search_url")
    @classmethod
    def sort_newest_first(cls, value: str) -> str:
        """Логика «новые на первой странице» держится на сортировке по дате добавления,
        а ссылка из адресной строки сайта её не содержит: добавляем сами."""
        parts = urlsplit(value)
        query = dict(parse_qsl(parts.query))
        query.setdefault("sort", NEWEST_FIRST_SORT)
        return urlunsplit(parts._replace(query=urlencode(query)))

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def proxy(self) -> ProxySettings | None:
        if not self.use_proxy:
            return None
        return ProxySettings()  # type: ignore[call-arg]  # values come from CIAN_PROXY_* env
