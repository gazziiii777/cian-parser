import atexit
import json
import logging
import logging.handlers
import queue
import socket
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import pygelf

from config.settings import LogLevel, Settings

logger = logging.getLogger(__name__)


@dataclass
class _Defaults:
    QUEUE_MAX_SIZE: int = 10_000
    NOISY_LIBRARIES: tuple[str, ...] = ("httpx", "httpcore", "asyncio")


class StreamFieldFilter(logging.Filter):
    """Ставит на запись имя потока и уровень словом: штатный level в GELF числовой,
    и в списке Graylog видно 4 вместо WARNING."""

    def __init__(self, stream_name: str) -> None:
        super().__init__()
        self._stream_name = stream_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.__dict__["stream"] = self._stream_name
        record.__dict__["severity"] = record.levelname
        return True


class JsonFormatter(logging.Formatter):
    """Одна строка, один JSON-объект с теми же именами полей, что Graylog показывает
    после разбора GELF, чтобы docker logs и интерфейс читались одинаково."""

    STANDARD_RECORD_KEYS: ClassVar[frozenset[str]] = frozenset(
        logging.LogRecord("", 0, "", 0, "", None, None).__dict__
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_RECORD_KEYS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class InProcessQueueHandler(logging.handlers.QueueHandler):
    """Кладёт запись в очередь как есть: базовый prepare форматирует её и обнуляет exc_info
    ради пересылки между процессами, а у нас очередь в том же процессе, и трейсбек нужен
    GELF-хендлеру отдельным полем."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


def _stdout_handler(level: LogLevel) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level.threshold())
    return handler


def _gelf_unavailable_reason(settings: Settings) -> str | None:
    """Нерезолвимый хост ловится здесь один раз: иначе исключение шло бы на каждой записи,
    и лог превращался бы в поток ошибок логирования."""
    if settings.gelf_host is None or settings.gelf_log_level is LogLevel.OFF:
        return "gelf host is not set or streaming is off"
    try:
        socket.getaddrinfo(settings.gelf_host, settings.gelf_port)
    except socket.gaierror:
        return f"gelf host {settings.gelf_host} is not resolvable"
    return None


def _gelf_handler(settings: Settings) -> logging.Handler:
    handler: logging.Handler = pygelf.GelfUdpHandler(
        host=settings.gelf_host,
        port=settings.gelf_port,
        debug=True,
        include_extra_fields=True,
    )
    handler.setLevel(settings.gelf_log_level.threshold())
    return handler


def configure_logging(settings: Settings) -> None:
    """JSON в stdout и, если задан хост, GELF UDP в Graylog. Оба хендлера сидят за очередью
    в отдельном потоке: сериализация и отправка идут вне event loop, и подвисший сокет
    не тормозит воркеры. Пороги у хендлеров свои, корень пропускает до меньшего из них.
    Причина отсутствия Graylog логируется после запуска слушателя, чтобы уйти JSON-строкой,
    а не plain text в stderr."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(min(settings.log_level.threshold(), settings.gelf_log_level.threshold()))

    targets = [_stdout_handler(level=settings.log_level)]
    gelf_problem = _gelf_unavailable_reason(settings=settings)
    if gelf_problem is None:
        targets.append(_gelf_handler(settings=settings))

    buffer: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=_Defaults.QUEUE_MAX_SIZE)
    queue_handler = InProcessQueueHandler(buffer)
    queue_handler.addFilter(StreamFieldFilter(stream_name=settings.graylog_stream))
    root.addHandler(queue_handler)
    listener = logging.handlers.QueueListener(buffer, *targets, respect_handler_level=True)
    listener.start()
    atexit.register(listener.stop)

    for name in _Defaults.NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

    if gelf_problem is not None:
        logger.info("%s, graylog streaming disabled", gelf_problem)
    else:
        logger.info(
            "gelf streaming enabled",
            extra={
                "gelf_host": settings.gelf_host,
                "gelf_port": settings.gelf_port,
                "graylog_stream": settings.graylog_stream,
            },
        )
